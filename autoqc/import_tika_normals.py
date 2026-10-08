"""Import Tika-reviewed, no-issue lifestyle videos as explicit normals.

The source database is read-only from this script. Videos remain artifact
references; no MP4 files are downloaded. The target AutoQC database receives
only rows whose latest MINIVET_THUMBNAIL review is exactly NO_ISSUES.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


WORKFLOWS = ("Zeus-Lightning-lifestyle", "Zeus-Lightning-lifestyle-2")
LABELS = {"bad_product": False, "bad_movement": False, "nudity": False}


def source_candidates(conn, workflow: str, limit: int):
    # DISTINCT ON chooses the final review for each generation. The artifact
    # service uses the generation UUID as the video artifact UUID.
    query = """
    WITH latest AS (
        SELECT DISTINCT ON (r.generation_id)
               r.generation_id, r.verdict, r.tag, r.updated_at
        FROM generation_reviews r
        JOIN generations g ON g.generation_id = r.generation_id
        WHERE r.tag = %s AND g.external_workflow_id = %s
        ORDER BY r.generation_id, r.updated_at DESC, r.generation_review_id DESC
    )
    SELECT g.generation_id, g.product_id, g.external_workflow_id,
           g.generation_payload, l.verdict, l.updated_at AS reviewed_at,
           p."FSN_ID" AS fsn_id, p."ITM_ID" AS item_id, p.title,
           p.category, a.artifact_type,
           image.image_urls,
           image.role AS image_role
    FROM latest l
    JOIN generations g ON g.generation_id = l.generation_id
    JOIN artifacts a ON a.artifact_id = g.generation_id
    LEFT JOIN products p ON p.product_id = g.product_id
    LEFT JOIN LATERAL (
        SELECT array_agg(pi.metadata->>'source_url'
                         ORDER BY (pi.role = 'catalog') DESC, pi.updated_at DESC)
                   FILTER (WHERE pi.metadata->>'source_url' IS NOT NULL) AS image_urls,
               max(pi.role) FILTER (WHERE pi.role = 'catalog') AS role
        FROM product_images pi
        WHERE pi.product_id = g.product_id
    ) image ON true
    WHERE l.verdict = %s
      AND a.artifact_type = 'VIDEO'
    ORDER BY g.generation_id
    LIMIT %s
    """
    return conn.execute(query, ("MINIVET_THUMBNAIL", workflow, ["NO_ISSUES"], limit)).fetchall()


def metadata(row):
    payload = row["generation_payload"] or {}
    return {
        key: value
        for key, value in {
            "product_id": str(row["product_id"]) if row["product_id"] else None,
            "fsn_id": row["fsn_id"] or payload.get("fsn_id"),
            "item_id": row["item_id"] or payload.get("itemid"),
            "product_title": row["title"],
            "product_category": row["category"],
            "workflow_id": row["external_workflow_id"],
            # Keep the first URL as the backwards-compatible primary image,
            # while preserving every product reference for bad-product checks.
            "image_url": (row["image_urls"] or [None])[0] if row["image_urls"] else None,
            "reference_image_urls": list(dict.fromkeys(row["image_urls"] or [])),
            "image_role": row["image_role"],
        }.items()
        if value not in (None, "")
    }


def import_normals(source_dsn: str, target_dsn: str, count: int, per_workflow: int):
    with psycopg.connect(source_dsn, row_factory=dict_row) as source:
        source.execute("SET statement_timeout = 120000")
        rows = []
        for workflow in WORKFLOWS:
            rows.extend(source_candidates(source, workflow, max(per_workflow * 3, 500)))

    with psycopg.connect(target_dsn, row_factory=dict_row) as target:
        existing = {
            str(row["artifact_id"])
            for row in target.execute("SELECT artifact_id FROM generations").fetchall()
        }
        selected = []
        per_workflow_counts = {workflow: 0 for workflow in WORKFLOWS}
        for row in rows:
            artifact_id = str(row["generation_id"])
            workflow = row["external_workflow_id"]
            if artifact_id in existing or per_workflow_counts.get(workflow, 0) >= per_workflow:
                continue
            selected.append(row)
            existing.add(artifact_id)
            per_workflow_counts[workflow] += 1
            if len(selected) >= count:
                break

        if len(selected) < count:
            raise RuntimeError(f"Only {len(selected)} eligible new normal videos found; need {count}")

        inserted = 0
        for row in selected:
            tags = {
                "set": "normal",
                "labels": dict(LABELS),
                "metadata": metadata(row),
                "provenance": {
                    "tika_review": {
                        "source": "video_generation.generation_reviews",
                        "tag": "MINIVET_THUMBNAIL",
                        "verdict": ["NO_ISSUES"],
                        "reviewed_at": row["reviewed_at"].isoformat(),
                    }
                },
            }
            result = target.execute(
                """INSERT INTO generations(uuid,artifact_id,tags)
                   VALUES(%s,%s,%s) ON CONFLICT(artifact_id) DO NOTHING""",
                (uuid4(), row["generation_id"], Jsonb(tags)),
            )
            inserted += result.rowcount
        target.commit()
    return {"selected": len(selected), "inserted": inserted, "by_workflow": per_workflow_counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dsn", default=os.getenv("TIKA_DATABASE_URL"), required=not bool(os.getenv("TIKA_DATABASE_URL")))
    parser.add_argument("--target-dsn", default=dotenv_values(".env").get("DATABASE_URL"))
    parser.add_argument("--count", type=int, default=600)
    parser.add_argument("--per-workflow", type=int, default=300)
    args = parser.parse_args()
    if not args.target_dsn:
        raise SystemExit("Target DATABASE_URL is missing")
    print(import_normals(args.source_dsn, args.target_dsn, args.count, args.per_workflow))


if __name__ == "__main__":
    main()
