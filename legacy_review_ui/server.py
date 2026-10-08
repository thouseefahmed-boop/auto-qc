#!/usr/bin/env python3
"""Local review UI for bad-product, bad-movement, and nudity datasets."""

from __future__ import annotations

import csv
import json
import mimetypes
import os
import re
import sqlite3
import ssl
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
EXPORT_ROOT = Path(os.environ.get("REVIEW_EXPORT_ROOT", str(ROOT.parent)))
WEB_ROOT = Path(os.environ.get("REVIEW_WEB_ROOT", str(ROOT / "web")))
REVIEWS_PATH = Path(os.environ.get("REVIEW_REVIEWS_PATH", str(ROOT / "reviews.json")))
IMAGE_MAP_PATH = Path(os.environ.get("REVIEW_IMAGE_MAP_PATH", str(ROOT / "product_images.json")))
THUMBNAIL_DB_PATH = Path(os.environ.get("REVIEW_THUMBNAIL_DB_PATH", str(ROOT / "thumbnail_issues.sqlite3")))
ARTIFACTS_BASE = "http://rtx-1.dev.internal:8090/artifacts-service"
THUMBNAIL_WORKFLOWS = {"Zeus-Lightning-lifestyle", "Zeus-Lightning-lifestyle-2"}
LOCK = threading.Lock()

DATASETS = {
    "bad_product": {
        "sources": [
            (
                EXPORT_ROOT / "bad_product_lifestyle_original.csv",
                EXPORT_ROOT / "final_datasets_verified" / "bad_product",
            ),
            (
                EXPORT_ROOT / "bad_product_lifestyle_reserve.csv",
                EXPORT_ROOT / "bad_product_lifestyle_reserve",
            ),
        ],
    },
    "bad_movement": {
        # Latest human-reviewed BAD_MOVEMENT videos from qc_review come first.
        # The older screening CSV remains below as a supplemental candidate
        # pool; every item still requires an explicit UI decision.
        "sources": [
            (
                EXPORT_ROOT / "qc_bad_movement.csv",
                EXPORT_ROOT / "qc_bad_movement",
            ),
            (
                EXPORT_ROOT / "movement_screening_apparel_500.csv",
                EXPORT_ROOT / "movement_screening_apparel_500",
            )
        ],
    },
    "nudity": {
        "sources": [(
            EXPORT_ROOT / "final_datasets_verified" / "nudity_thigh_or_contour.csv",
            EXPORT_ROOT / "final_datasets_verified" / "nudity",
        )],
    },
}

_ONLY_CATEGORY = os.environ.get("REVIEW_ONLY_CATEGORY", "").strip()
if _ONLY_CATEGORY in DATASETS:
    DATASETS = {_ONLY_CATEGORY: DATASETS[_ONLY_CATEGORY]}


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def save_reviews(reviews: dict) -> None:
    temporary = REVIEWS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(reviews, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, REVIEWS_PATH)


def artifact_id(row: dict) -> str:
    return row.get("video_artifact_id") or row.get("artifact_id") or row.get("generation_id") or ""


def locate_video(media_dir: Path, identifier: str) -> Path | None:
    matches = sorted(media_dir.glob(f"*_{identifier}.mp4"))
    return matches[0] if matches else None


def build_items() -> list[dict]:
    image_map = load_json(IMAGE_MAP_PATH, {})
    items = []
    seen = set()
    for category, config in DATASETS.items():
        position = 0
        for source_index, (csv_path, media_dir) in enumerate(config["sources"]):
            if not csv_path.exists():
                continue
            with csv_path.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            for row in rows:
                position += 1
                identifier = artifact_id(row)
                item_key = f"{category}:{identifier}"
                if not identifier or item_key in seen:
                    continue
                seen.add(item_key)
                video = locate_video(media_dir, identifier)
                product_key = row.get("product_id", "")
                image = image_map.get(product_key, {}) if category != "nudity" else {}
                reference_urls = image.get("source_urls") or image.get("image_urls") or []
                if not isinstance(reference_urls, list):
                    reference_urls = [image.get("source_url")] if image.get("source_url") else []
                if not reference_urls and row.get("image_url"):
                    reference_urls = [row["image_url"]]
                reference_urls = list(dict.fromkeys(
                    url for url in reference_urls
                    if isinstance(url, str) and url.startswith(("http://", "https://"))
                ))
                exposure_flags = []
                if category == "nudity":
                    for field, label in (
                        ("privates_exposed", "Private parts exposed"),
                        ("midriff_exposure", "Midriff exposure"),
                        ("visible_thighs", "Visible thighs"),
                        ("garment_shifted", "Garment shifted"),
                        ("coverage_reduced_vs_reference", "Coverage reduced vs reference"),
                        ("body_contour_visible", "Body contour visible"),
                    ):
                        if str(row.get(field, "")).strip().lower() in {"t", "true", "1", "yes"}:
                            exposure_flags.append(label)
                items.append({
                    "id": f"{category}:{identifier}",
                    "category": category,
                    "position": position,
                    "artifact_id": identifier,
                    "product_id": product_key,
                    "fsn_id": row.get("fsn_id", ""),
                    "item_id": row.get("item_id", ""),
                    "title": row.get("product_title", "") or row.get("category", "") or "Untitled product",
                    "product_category": row.get("product_category", "") or row.get("category", ""),
                    "source_label": (
                        "Visible thighs · RISKY"
                        if category == "nudity" and str(row.get("visible_thighs", "")).strip().lower() in {"t", "true", "1", "yes"}
                        else "Body contour visible · RISKY"
                        if category == "nudity" and str(row.get("body_contour_visible", "")).strip().lower() in {"t", "true", "1", "yes"}
                        else row.get("review_verdicts", "") or row.get("safety_decision", "")
                    ),
                    "exposure_flags": exposure_flags,
                    "reviewed_at": row.get("reviewed_at", "") or row.get("qc_updated_at", ""),
                    "video_available": bool(video) or bool(re.fullmatch(r"[0-9a-fA-F-]{36}", identifier)),
                    # QC-review exports can carry the artifact-service URL directly.
                    # This is needed for older QC artifacts that are not present in
                    # the newer /artifacts-service registry used by /s3/.
                    "video_url": row.get("video_url") or (f"/media/{category}/{source_index}/{quote(video.name)}" if video else (f"/s3/{quote(identifier)}" if re.fullmatch(r"[0-9a-fA-F-]{36}", identifier) else "")),
                    "image_url": reference_urls[0] if reference_urls else image.get("source_url", ""),
                    "image_urls": reference_urls,
                    "image_role": image.get("role", ""),
                })
    return items


class ReviewHandler(SimpleHTTPRequestHandler):
    server_version = "DatasetReview/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def json_response(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/items":
            return self.get_items(parse_qs(parsed.query))
        if parsed.path == "/api/export":
            return self.export_reviews()
        if parsed.path.startswith("/s3/"):
            return self.stream_s3_video(parsed.path)
        if parsed.path.startswith("/media/"):
            return self.serve_media(parsed.path)
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != "/api/review":
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            item_id = str(payload["item_id"])
            decision = str(payload["decision"])
            reviewer = ""
            if decision not in {"approved", "rejected", "unreviewed"}:
                raise ValueError("invalid decision")
        except (KeyError, ValueError, json.JSONDecodeError):
            return self.json_response({"error": "Invalid review payload"}, HTTPStatus.BAD_REQUEST)

        if ":bulk:" in item_id:
            category, artifact = item_id.split(":bulk:", 1)
            if category not in {"bad_product", "bad_movement"}:
                return self.json_response({"error": "Invalid review category"}, HTTPStatus.BAD_REQUEST)
            if not re.fullmatch(r"[0-9a-fA-F-]{36}", artifact):
                return self.json_response({"error": "Invalid artifact ID"}, HTTPStatus.BAD_REQUEST)
            with sqlite3.connect(THUMBNAIL_DB_PATH) as db:
                if decision == "unreviewed":
                    db.execute("delete from thumbnail_queue_reviews where category = ? and artifact_id = ?", (category, artifact))
                else:
                    db.execute(
                        """insert into thumbnail_queue_reviews (category, artifact_id, decision, note, reviewer, updated_at)
                           values (?, ?, ?, ?, ?, ?) on conflict(category, artifact_id) do update set
                           decision=excluded.decision, note=excluded.note, reviewer=excluded.reviewer, updated_at=excluded.updated_at""",
                        (category, artifact, decision, str(payload.get("note", "")).strip(), reviewer, datetime.now(timezone.utc).isoformat()),
                    )
            return self.json_response({"ok": True})

        with LOCK:
            reviews = load_json(REVIEWS_PATH, {})
            if decision == "unreviewed":
                reviews.pop(item_id, None)
            else:
                reviews[item_id] = {
                    "decision": decision,
                    "note": str(payload.get("note", "")).strip(),
                    "reviewer": reviewer,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            save_reviews(reviews)
        return self.json_response({"ok": True, "review": reviews.get(item_id)})

    def get_items(self, query):
        category = query.get("category", ["bad_product"])[0]
        if (category in {"bad_product", "bad_movement"}
                and THUMBNAIL_DB_PATH.exists()
                and os.environ.get("REVIEW_DISABLE_BULK_DB", "") != "1"):
            return self.get_merged_items(query)
        status = query.get("status", ["all"])[0]
        search = query.get("search", [""])[0].strip().lower()
        reviews = load_json(REVIEWS_PATH, {})
        items = []
        for item in build_items():
            if item["category"] != category:
                continue
            review = reviews.get(item["id"], {})
            item["decision"] = review.get("decision", "unreviewed")
            item["note"] = review.get("note", "")
            if status != "all" and item["decision"] != status:
                continue
            haystack = " ".join(str(value) for value in item.values()).lower()
            if search and search not in haystack:
                continue
            items.append(item)
        counts = {"all": 0, "unreviewed": 0, "approved": 0, "rejected": 0}
        for item in build_items():
            if item["category"] == category:
                counts["all"] += 1
                counts[reviews.get(item["id"], {}).get("decision", "unreviewed")] += 1
        # Dedicated queues do not use the legacy SQLite bulk pool, but the
        # frontend still expects a one-item page and a total for keyboard
        # navigation.  Filter first, then return only the requested position.
        try:
            index = max(0, int(query.get("index", ["0"])[0]))
        except ValueError:
            return self.json_response({"error": "Invalid index"}, HTTPStatus.BAD_REQUEST)
        total = len(items)
        page = items[index:index + 1] if category in {"bad_product", "bad_movement"} else items
        self.json_response({"items": page, "counts": counts, "total": total})

    def get_merged_items(self, query):
        category = query.get("category", ["bad_product"])[0]
        status = query.get("status", ["all"])[0]
        search = query.get("search", [""])[0].strip()
        try:
            index = max(0, int(query.get("index", ["0"])[0]))
        except ValueError:
            return self.json_response({"error": "Invalid index"}, HTTPStatus.BAD_REQUEST)
        reviews = load_json(REVIEWS_PATH, {})
        local_items = []
        local_counts = {"all": 0, "unreviewed": 0, "approved": 0, "rejected": 0}
        for item in build_items():
            if item["category"] != category:
                continue
            review = reviews.get(item["id"], {})
            item["decision"] = review.get("decision", "unreviewed")
            item["note"] = review.get("note", "")
            local_counts["all"] += 1
            local_counts[item["decision"]] += 1
            haystack = " ".join(str(value) for value in item.values()).lower()
            if status != "all" and item["decision"] != status:
                continue
            if search and search.lower() not in haystack:
                continue
            local_items.append(item)

        search_sql = ""
        parameters = [category]
        if search:
            search_sql = " and (i.fsn_id like ? or i.item_id like ? or i.product_title like ? or i.product_category like ? or i.workflow_id like ?)"
            parameters.extend([f"%{search}%"] * 5)
        status_sql = ""
        if status != "all":
            status_sql = " and coalesce(r.decision, 'unreviewed') = ?"
            parameters.append(status)
        with sqlite3.connect(THUMBNAIL_DB_PATH) as db:
            total = db.execute(
                "select count(*) from thumbnail_items i left join thumbnail_queue_reviews r on r.artifact_id=i.artifact_id and r.category=? where 1=1" + search_sql + status_sql,
                parameters,
            ).fetchone()[0]
            count_rows = db.execute(
                """select coalesce(r.decision, 'unreviewed'), count(*) from thumbnail_items i
                   left join thumbnail_queue_reviews r on r.artifact_id=i.artifact_id and r.category=? group by 1""",
                (category,),
            ).fetchall()
            remote_counts = {"all": 0, "unreviewed": 0, "approved": 0, "rejected": 0}
            for decision, count in count_rows:
                remote_counts[decision] = count
                remote_counts["all"] += count
            remote_index = index - len(local_items)
            row = None
            if remote_index >= 0:
                remote_parameters = [category]
                if search:
                    remote_parameters.extend([f"%{search}%"] * 5)
                if status != "all":
                    remote_parameters.append(status)
                row = db.execute(
                    """select i.artifact_id, i.workflow_id, i.product_id, i.fsn_id, i.item_id,
                          i.product_title, i.product_category, i.review_verdicts, i.reviewed_at,
                          i.image_url, i.image_role, coalesce(r.decision, 'unreviewed'), coalesce(r.note, '')
                       from thumbnail_items i left join thumbnail_queue_reviews r
                         on r.artifact_id=i.artifact_id and r.category=?
                       where 1=1""" + search_sql + status_sql + " order by i.position limit 1 offset ?",
                    remote_parameters + [remote_index],
                ).fetchone()
        counts = {key: local_counts[key] + remote_counts[key] for key in local_counts}
        if index < len(local_items):
            local_item = local_items[index]
            self.json_response({"items": [local_item], "counts": counts, "total": len(local_items) + total})
            return
        items = []
        if row:
            (artifact, workflow, product_id, fsn, item_id, title, product_category,
             verdict, reviewed_at, image_url, image_role, decision, note) = row
            video_url = f"/s3/{quote(artifact)}"
            items.append({
                "id": f"{category}:bulk:{artifact}", "category": category,
                "position": index + 1, "artifact_id": artifact, "product_id": product_id,
                "fsn_id": fsn, "item_id": item_id, "title": title or "Untitled product",
                "product_category": product_category, "source_label": verdict,
                "workflow_id": workflow, "reviewed_at": reviewed_at,
                "video_available": bool(video_url), "video_url": video_url,
                "image_url": image_url, "image_role": image_role,
                "decision": decision, "note": note,
            })
            self.json_response({"items": items, "counts": counts, "total": len(local_items) + total})

    @staticmethod
    def presigned_video_url(artifact):
        query = "tenant_id=Flipkart&delivery_mode=presigned"
        endpoint = f"{ARTIFACTS_BASE}/internal/artifacts/{quote(artifact)}/download?{query}"
        with urlopen(endpoint, timeout=30) as response:
            payload = json.loads(response.read())
        return payload["download_url"]

    def stream_s3_video(self, request_path):
        artifact = request_path.removeprefix("/s3/")
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", artifact):
            return self.send_error(HTTPStatus.BAD_REQUEST)
        headers_sent = False
        try:
            signed_url = self.presigned_video_url(artifact)
            headers = {"User-Agent": "LifestyleVideoReview/1"}
            range_header = self.headers.get("Range")
            if range_header:
                headers["Range"] = range_header
            request = Request(signed_url, headers=headers)
            with urlopen(request, timeout=60, context=ssl._create_unverified_context()) as response:
                status = response.status
                self.send_response(status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "video/mp4"))
                self.send_header("Accept-Ranges", response.headers.get("Accept-Ranges", "bytes"))
                for header in ("Content-Length", "Content-Range", "ETag", "Last-Modified"):
                    value = response.headers.get(header)
                    if value:
                        self.send_header(header, value)
                self.send_header("Cache-Control", "private, max-age=60")
                self.end_headers()
                headers_sent = True
                while chunk := response.read(1024 * 1024):
                    self.wfile.write(chunk)
        except Exception as exc:
            print(f"S3 playback failed for {artifact}: {exc}")
            if not headers_sent:
                try:
                    self.send_error(HTTPStatus.BAD_GATEWAY, "Could not stream video from S3")
                except (BrokenPipeError, ConnectionResetError):
                    pass

    def export_reviews(self):
        reviews = load_json(REVIEWS_PATH, {})
        indexed = {item["id"]: item for item in build_items()}
        rows = []
        for item_id, review in reviews.items():
            item = indexed.get(item_id, {})
            rows.append({**item, **review})
        if THUMBNAIL_DB_PATH.exists():
            with sqlite3.connect(THUMBNAIL_DB_PATH) as db:
                db.row_factory = sqlite3.Row
                for row in db.execute(
                    """select i.*, r.category, r.decision, r.note, r.reviewer, r.updated_at as review_updated_at
                       from thumbnail_queue_reviews r join thumbnail_items i using (artifact_id)
                       order by r.category, i.position"""
                ):
                    record = dict(row)
                    record.update({
                        "id": f"{record['category']}:bulk:{record['artifact_id']}",
                        "video_url": "",
                        "video_available": True,
                    })
                    rows.append(record)
        body = json.dumps(rows, indent=2, ensure_ascii=False).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Disposition", "attachment; filename=manual_reviews.json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_media(self, request_path: str):
        parts = request_path.split("/")
        if len(parts) != 5 or parts[2] not in DATASETS:
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            source_index = int(parts[3])
        except ValueError:
            return self.send_error(HTTPStatus.BAD_REQUEST)
        filename = parts[4]
        if Path(filename).name != filename:
            return self.send_error(HTTPStatus.BAD_REQUEST)
        sources = DATASETS[parts[2]]["sources"]
        if 0 <= source_index < len(sources):
            path = sources[source_index][1] / filename
        else:
            # Keep already-open browser tabs working after the media route was
            # changed from item position to source index.
            path = next(
                (media_dir / filename for _, media_dir in sources if (media_dir / filename).is_file()),
                sources[0][1] / filename,
            )
        if not path.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        size = path.stat().st_size
        start, end = 0, size - 1
        match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if match:
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = min(int(match.group(2)), end)
            if start > end or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        else:
            status = HTTPStatus.OK
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def main():
    port = int(os.environ.get("REVIEW_UI_PORT", "8765"))
    with sqlite3.connect(THUMBNAIL_DB_PATH) as db:
        db.execute("""create table if not exists thumbnail_queue_reviews (
            category text not null check(category in ('bad_product','bad_movement')),
            artifact_id text not null,
            decision text not null check(decision in ('approved','rejected')),
            note text not null default '', reviewer text not null default '', updated_at text not null,
            primary key(category, artifact_id))""")
        columns = {row[1] for row in db.execute("pragma table_info(thumbnail_queue_reviews)")}
        if "reviewer" not in columns:
            db.execute("alter table thumbnail_queue_reviews add column reviewer text not null default ''")
    server = ThreadingHTTPServer(("0.0.0.0", port), ReviewHandler)
    print(f"Review UI: http://0.0.0.0:{port}")
    print(f"Reviews:  {REVIEWS_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
