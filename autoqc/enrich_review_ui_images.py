"""Enrich the legacy lifestyle review UI's product image map.

The old review UI historically stored only one ``source_url`` per product.
This utility keeps that field as the primary image and adds ``source_urls``
with every catalog/reference image from the read-only video-generation DB.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import psycopg


def product_ids(export_root: Path) -> set[str]:
    ids: set[str] = set()
    paths = list(export_root.glob("*.csv")) + list((export_root / "final_datasets_verified").glob("*.csv"))
    for path in paths:
        with path.open(newline="") as stream:
            for row in csv.DictReader(stream):
                if row.get("product_id"):
                    ids.add(row["product_id"])
    return ids


def enrich(source_dsn: str, export_root: Path) -> dict[str, int]:
    image_path = export_root / "review_ui" / "product_images.json"
    data = json.loads(image_path.read_text()) if image_path.exists() else {}
    ids = sorted(product_ids(export_root))
    grouped: dict[str, list[dict[str, str]]] = {}
    with psycopg.connect(source_dsn) as source:
        rows = source.execute(
            """
            SELECT product_id::text, metadata->>'source_url' AS source_url, role
            FROM product_images
            WHERE product_id::text = ANY(%s)
              AND metadata->>'source_url' IS NOT NULL
            ORDER BY product_id, (role = 'catalog') DESC, updated_at DESC
            """,
            (ids,),
        ).fetchall()
    for product_id, url, role in rows:
        grouped.setdefault(product_id, []).append({"url": url, "role": role or ""})

    updated = 0
    multi = 0
    for product_id, refs in grouped.items():
        urls = list(dict.fromkeys(ref["url"] for ref in refs if ref["url"]))
        if not urls:
            continue
        old = json.dumps(data.get(product_id, {}), sort_keys=True)
        entry = dict(data.get(product_id, {}))
        entry["source_url"] = urls[0]
        entry["source_urls"] = urls
        entry["role"] = next((ref["role"] for ref in refs if ref["role"] == "catalog"), refs[0]["role"])
        data[product_id] = entry
        multi += len(urls) > 1
        updated += json.dumps(entry, sort_keys=True) != old

    backup = image_path.with_suffix(".json.before-multi-refs")
    if image_path.exists() and not backup.exists():
        backup.write_bytes(image_path.read_bytes())
    temporary = image_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    temporary.replace(image_path)
    return {"products_in_csv": len(ids), "products_with_references": len(grouped),
            "products_with_multiple_references": multi, "products_updated": updated}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dsn", required=True)
    parser.add_argument("--export-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(enrich(args.source_dsn, args.export_root), indent=2))


if __name__ == "__main__":
    main()
