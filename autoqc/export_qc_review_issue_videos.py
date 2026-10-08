"""Export the latest QC-reviewed videos for a single issue into a review CSV.

The qc_review database is read-only. The output is a local queue manifest for
the legacy review UI; it contains artifact IDs and metadata only, not video
bytes.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import psycopg


FIELDS = (
    "dataset_label", "generation_id", "product_id", "fsn_id", "item_id",
    "product_title", "product_category", "artifact_id", "video_artifact_id",
    "video_url", "image_url", "review_verdicts", "reviewed_at", "review_type",
)


def export_issue(dsn: str, output: Path, issue: str, limit: int | None = None) -> dict[str, int]:
    limit_sql = " LIMIT %s" if limit else ""
    params: list[object] = [issue]
    if limit:
        params.append(limit)
    query = f"""
        WITH latest AS (
            SELECT DISTINCT ON (item_id) item_id, verdict, updated_at
            FROM item_reviews
            ORDER BY item_id, updated_at DESC, item_review_id DESC
        )
        SELECT i.item_id, i.product_id, i.review_type, i.category,
               i.item_payload, latest.verdict, latest.updated_at
        FROM items i
        JOIN latest ON latest.item_id = i.item_id
        WHERE %s = ANY(latest.verdict)
          AND (i.item_payload ? 'video_artifact_id'
               OR i.item_payload ? 'stitched_video_artifact_id'
               OR i.item_payload ? 'output_video_artifact_ids')
        ORDER BY latest.updated_at, i.item_id
        {limit_sql}
    """
    rows = []
    with psycopg.connect(dsn) as conn:
        for item_id, product_id, review_type, category, payload, verdict, reviewed_at in conn.execute(query, params):
            payload = payload or {}
            metadata = payload.get("metadata") or {}
            artifact = payload.get("video_artifact_id") or payload.get("stitched_video_artifact_id")
            if not artifact:
                outputs = payload.get("output_video_artifact_ids") or []
                artifact = outputs[0] if outputs else ""
            if not artifact:
                continue
            rows.append({
                "dataset_label": issue.lower(),
                "generation_id": str(item_id),
                # This is the catalog/item identifier when the older review
                # record does not have a UUID product row.
                "product_id": str(product_id or payload.get("product_id") or ""),
                "fsn_id": str((metadata.get("product_metadata") or {}).get("fsn_id") or payload.get("fsn_id") or ""),
                "item_id": str(product_id or ""),
                "product_title": str(metadata.get("title") or metadata.get("puppet_name") or ""),
                "product_category": str(metadata.get("category") or category or ""),
                "artifact_id": str(artifact),
                "video_artifact_id": str(artifact),
                # These QC artifacts are served by the legacy artifact gateway.
                # Keep the URL in the manifest so the review UI does not attempt
                # to resolve old IDs through the newer artifact-service registry.
                "video_url": f"http://10.12.46.7:30080/artifacts/{artifact}/file?tenant_id=Flipkart",
                "image_url": str(metadata.get("image_link") or payload.get("image_url") or ""),
                "review_verdicts": "|".join(str(v) for v in verdict),
                "reviewed_at": reviewed_at.isoformat() if reviewed_at else "",
                "review_type": str(review_type or ""),
            })
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)
    return {"exported": len(rows), "unique_videos": len({row["artifact_id"] for row in rows})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--issue", default="BAD_MOVEMENT")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(json.dumps(export_issue(args.dsn, args.output, args.issue, args.limit), indent=2))


if __name__ == "__main__":
    main()
