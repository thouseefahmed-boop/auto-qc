"""Add every product reference image to existing AutoQC generation metadata.

The source database is read-only.  Only JSON metadata in the target AutoQC
database is updated; labels, provenance, and benchmark snapshots are kept
unchanged.  This makes old imported rows usable by the multi-reference
bad-product prompt without re-importing or re-reviewing them.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def source_images(conn, product_ids: list[str]) -> dict[str, list[str]]:
    if not product_ids:
        return {}
    rows = conn.execute(
        """
        SELECT product_id::text AS product_id,
               array_agg(metadata->>'source_url'
                         ORDER BY (role = 'catalog') DESC, updated_at DESC)
                 FILTER (WHERE metadata->>'source_url' IS NOT NULL) AS image_urls
        FROM product_images
        WHERE product_id::text = ANY(%s)
        GROUP BY product_id
        """,
        (product_ids,),
    ).fetchall()
    return {
        row["product_id"]: list(dict.fromkeys(row["image_urls"] or []))
        for row in rows
    }


def backfill(source_dsn: str, target_dsn: str, batch_size: int = 500) -> dict[str, int]:
    with psycopg.connect(target_dsn, row_factory=dict_row) as target:
        rows = target.execute(
            "SELECT uuid, tags FROM generations WHERE tags->'metadata'->>'product_id' IS NOT NULL"
        ).fetchall()
        by_product: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_product[row["tags"]["metadata"]["product_id"]].append(row)

        updated = 0
        products = 0
        with psycopg.connect(source_dsn, row_factory=dict_row) as source:
            product_ids = list(by_product)
            for start in range(0, len(product_ids), batch_size):
                refs = source_images(source, product_ids[start:start + batch_size])
                products += len(refs)
                for product_id, image_urls in refs.items():
                    if not image_urls:
                        continue
                    for row in by_product[product_id]:
                        tags = row["tags"]
                        metadata = tags.setdefault("metadata", {})
                        old = json.dumps(metadata, sort_keys=True)
                        metadata["reference_image_urls"] = image_urls
                        metadata["image_url"] = image_urls[0]
                        if json.dumps(metadata, sort_keys=True) == old:
                            continue
                        target.execute(
                            "UPDATE generations SET tags=%s, updated_at=clock_timestamp() WHERE uuid=%s",
                            (Jsonb(tags), row["uuid"]),
                        )
                        updated += 1
        target.commit()
    return {"generations_scanned": len(rows), "products_found": products, "generations_updated": updated}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dsn", required=True)
    parser.add_argument("--target-dsn", required=True)
    args = parser.parse_args()
    print(json.dumps(backfill(args.source_dsn, args.target_dsn), indent=2))


if __name__ == "__main__":
    main()
