"""Read-only import of existing approvals. No legacy file is ever modified."""
import argparse
import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .db import initialize, pool
from .models import LABELS, set_for

META_FIELDS = ("product_id", "fsn_id", "item_id", "product_title", "product_category", "workflow_id", "image_url", "image_role")


def load_source(root: Path):
    review_path = root / "review_ui" / "reviews.json"
    db_path = root / "review_ui" / "thumbnail_issues.sqlite3"
    decisions = {}

    def add(category, identifier, value, source):
        if category not in LABELS:
            return
        try:
            identifier = str(UUID(identifier))
        except (ValueError, TypeError):
            return
        entry = {"decision": value.get("decision"), "updated_at": value.get("updated_at", ""),
                 "note": value.get("note", ""), "source": source}
        key = (category, identifier)
        if key not in decisions or entry["updated_at"] > decisions[key]["updated_at"]:
            decisions[key] = entry

    if review_path.exists():
        for key, value in json.loads(review_path.read_text()).items():
            if ":" in key:
                category, identifier = key.split(":", 1)
                add(category, identifier, value, "legacy_reviews_json")
    metadata = {}
    if db_path.exists():
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")  # Stable SQLite snapshot, including live WAL.
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "thumbnail_queue_reviews" in tables:
                for row in db.execute("SELECT category,artifact_id,decision,note,updated_at FROM thumbnail_queue_reviews"):
                    row = dict(row)
                    add(row["category"], row["artifact_id"], row, "thumbnail_queue_reviews")
            approved_ids = {identifier for (_, identifier), d in decisions.items() if d["decision"] == "approved"}
            if "thumbnail_items" in tables:
                for start in range(0, len(approved_ids), 500):
                    ids = sorted(approved_ids)[start:start + 500]
                    placeholders = ",".join("?" for _ in ids)
                    for row in db.execute(f"SELECT * FROM thumbnail_items WHERE artifact_id IN ({placeholders})", ids):
                        metadata[row["artifact_id"]] = {k: row[k] for k in META_FIELDS if k in row.keys() and row[k]}
    ids = {identifier for (_, identifier), d in decisions.items() if d["decision"] == "approved"}
    # Selected manifests only. Do not scan or read any MP4 files.
    names = ("bad_product_lifestyle_original.csv", "bad_product_lifestyle_reserve.csv",
             "movement_screening_apparel_500.csv", "nudity_200.csv", "nudity_privates_exposed_78.csv",
             "nudity_risky_extra_1000.csv", "nudity_visible_thighs_426.csv", "nudity_thigh_or_contour.csv")
    for name in names:
        for path in (root / name, root / "final_datasets_verified" / name):
            if not path.exists():
                continue
            with path.open(newline="") as stream:
                for row in csv.DictReader(stream):
                    identifier = row.get("video_artifact_id") or row.get("artifact_id") or row.get("generation_id")
                    if identifier in ids:
                        new = {k: row[k] for k in META_FIELDS if row.get(k)}
                        metadata.setdefault(identifier, {}).update(new)
    image_path = root / "review_ui" / "product_images.json"
    if image_path.exists():
        images = json.loads(image_path.read_text())
        for meta in metadata.values():
            image = images.get(meta.get("product_id"), {})
            urls = image.get("source_urls") or image.get("image_urls") or image.get("urls")
            if not isinstance(urls, list):
                urls = [image.get("source_url")] if image.get("source_url") else []
            urls = list(dict.fromkeys(url for url in urls if isinstance(url, str) and url))
            if urls:
                meta["image_url"] = urls[0]
                meta["reference_image_urls"] = urls
                meta["image_role"] = image.get("role", "")
    return decisions, metadata


def import_source(root: Path):
    decisions, metadata = load_source(root)
    grouped = {}
    for (label, identifier), decision in decisions.items():
        grouped.setdefault(identifier, {})[label] = decision
    summary = Counter()
    with pool.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(582904402)")
        for identifier, values in grouped.items():
            row = conn.execute("SELECT * FROM generations WHERE artifact_id=%s FOR UPDATE", (identifier,)).fetchone()
            positives = {label for label, decision in values.items() if decision["decision"] == "approved"}
            if not row and not positives:
                continue
            if row and "manual" in row["tags"].get("provenance", {}):
                summary["manual_preserved"] += 1
                continue
            tags = row["tags"] if row else {"labels": {label: None for label in LABELS}, "provenance": {}, "metadata": {}}
            before = json.dumps(tags, sort_keys=True)
            for label, decision in values.items():
                if decision["decision"] == "approved":
                    tags["labels"][label] = True
                    tags["provenance"][label] = decision
                elif label in tags["provenance"]:
                    # Rejecting a candidate withdraws the positive approval. It
                    # does not establish a confirmed-negative or normal video.
                    tags["labels"][label] = None
                    tags["provenance"][label] = decision
            tags["metadata"].update(metadata.get(identifier, {}))
            tags["set"] = set_for(tags["labels"])
            if tags["labels"].get("nudity") is True:
                tags["label_policy"] = "Legacy exposure approval: may include visible_thighs or body_contour_visible; not necessarily explicit nudity."
            if not row:
                conn.execute("INSERT INTO generations(uuid,artifact_id,tags) VALUES(%s,%s,%s)",
                             (uuid4(), identifier, Jsonb(tags)))
                summary["inserted"] += 1
            elif before != json.dumps(tags, sort_keys=True):
                conn.execute("UPDATE generations SET tags=%s,updated_at=clock_timestamp() WHERE uuid=%s", (Jsonb(tags), row["uuid"]))
                summary["updated"] += 1
            else:
                summary["unchanged"] += 1
            for label in positives:
                summary[label] += 1
    return dict(summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    initialize()
    try:
        print(json.dumps(import_source(args.source), indent=2))
    finally:
        pool.close()


if __name__ == "__main__":
    main()
