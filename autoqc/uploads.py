"""Upload videos to MiniVET's existing artifact store and freeze their selection."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from starlette.exceptions import HTTPException as StarletteHTTPException

from .db import pool
from .models import LABELS, VideoTestsetIn

router = APIRouter()
VIDEO_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}


def upload_limits():
    return {"max_files": 100, "max_bytes": int(os.getenv("AUTOQC_UPLOAD_MAX_BYTES", str(500 * 1024 * 1024))),
            "extensions": list(VIDEO_TYPES)}


router.add_api_route("/api/upload-limits", upload_limits, methods=["GET"])


def inspect_video(path):
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-show_entries", "stream=codec_type,width,height:format=duration",
            "-of", "json", str(path),
        ], capture_output=True, timeout=20, check=True)
        info = json.loads(result.stdout)
        stream = next(s for s in info["streams"] if s.get("codec_type") == "video"
                      and s.get("width", 0) > 0 and s.get("height", 0) > 0)
        duration = float(info.get("format", {}).get("duration", 0))
        if duration <= 0:
            raise ValueError("No playable duration")
        return {"duration_seconds": duration, "width": stream["width"], "height": stream["height"]}
    except FileNotFoundError as exc:
        raise HTTPException(503, "Video uploads are unavailable: the video validator is not installed") from exc
    except (subprocess.SubprocessError, ValueError, KeyError, StopIteration) as exc:
        raise HTTPException(422, "This file does not contain a readable video. Choose an MP4, MOV, or WebM video.") from exc


def store_video(upload):
    # Treat the supplied name only as display metadata, never as a local path.
    filename = (upload.filename or "").replace("\\", "/").split("/")[-1][:240]
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_TYPES:
        raise HTTPException(422, "Choose an MP4, MOV, or WebM video")
    digest = hashlib.sha256()
    size = 0
    with tempfile.NamedTemporaryFile(suffix=suffix) as staged:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > upload_limits()["max_bytes"]:
                raise HTTPException(413, "This video exceeds the upload size limit")
            digest.update(chunk)
            staged.write(chunk)
        if not size:
            raise HTTPException(422, "This video is empty")
        staged.flush()
        video_info = inspect_video(staged.name)
        checksum = digest.hexdigest()
        metadata = {"filename": filename, "product_title": filename, "upload_source": "autoqc_upload",
                    "sha256": checksum, "size_bytes": size, **video_info}
        # Upload outside any DB transaction. Content-based idempotency makes a
        # retry after a lost response reuse the artifact rather than store it twice.
        staged.seek(0)
        base = os.getenv("ARTIFACTS_BASE", "http://rtx-1.dev.internal:8090/artifacts-service").rstrip("/")
        try:
            with httpx.Client(timeout=httpx.Timeout(600, connect=10)) as client:
                response = client.post(base + "/internal/artifacts/upload-multipart", data={
                    "tenant_id": os.getenv("ARTIFACT_TENANT", "Flipkart"),
                    "external_id": "autoqc-video-" + checksum, "source": "autoqc_upload",
                    "artifact_type": "VIDEO", "version": "1", "visibility": "PRIVATE",
                    "filename": filename, "content_type": VIDEO_TYPES[suffix],
                    "metadata_json": json.dumps(metadata),
                }, files={"file": (filename, staged, VIDEO_TYPES[suffix])})
                response.raise_for_status()
                artifact_id = UUID(response.json()["artifact_id"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(502, "Video storage is unavailable. Retry this upload; completed uploads are retained.") from exc
    tags = {"set": "unclassified", "labels": dict.fromkeys(LABELS), "metadata": metadata,
            "provenance": {"upload": {"source": "autoqc_upload", "sha256": checksum}}}
    with pool.connection() as conn:
        # A duplicate upload must not reset an already reviewed video's labels.
        conn.execute("INSERT INTO generations(uuid,artifact_id,tags) VALUES(%s,%s,%s) ON CONFLICT(artifact_id) DO NOTHING",
                     (uuid4(), artifact_id, Jsonb(tags)))
        return conn.execute("SELECT * FROM generations WHERE artifact_id=%s", (artifact_id,)).fetchone()


class _UploadTooLarge(MultiPartException):
    def __init__(self):
        super().__init__("Upload exceeds the size limit")


@router.post("/api/videos/upload", status_code=201)
async def upload_video(request: Request):
    # Bound the body during multipart parsing, including chunked requests, so a
    # rejected oversized upload cannot fill the temporary spool before validation.
    max_body = upload_limits()["max_bytes"] + 64 * 1024
    received = 0

    async def receive():
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > max_body:
            raise _UploadTooLarge()
        return message

    limited = Request(request.scope, receive=receive)
    try:
        async with limited.form(max_files=1, max_fields=0) as form:
            files = form.getlist("file")
            if len(form) != 1 or len(files) != 1 or not isinstance(files[0], UploadFile):
                raise HTTPException(422, "Choose one video per upload request")
            return await run_in_threadpool(store_video, files[0])
    except _UploadTooLarge as exc:
        raise HTTPException(413, "This video exceeds the upload size limit") from exc
    except StarletteHTTPException as exc:
        if received > max_body:
            raise HTTPException(413, "This video exceeds the upload size limit") from exc
        raise


@router.post("/api/testsets/from-videos", status_code=201)
def create_video_testset(payload: VideoTestsetIn):
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Enter a testset name")
    ids = list(dict.fromkeys(payload.generation_ids))
    query = {"source": "upload", "generation_ids": [str(i) for i in ids], "tags": []}
    with pool.connection() as conn:
        # Serialize retries of this creation only; never hold a lock for uploads.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (payload.request_id.int % (2**63 - 1),))
        existing = conn.execute("SELECT * FROM testsets WHERE uuid=%s", (payload.request_id,)).fetchone()
        if existing:
            if existing["name"] != name or existing["query"] != query:
                raise HTTPException(409, "This testset creation ID was already used for another selection")
            return existing
        rows = {row["uuid"]: row for row in conn.execute("SELECT * FROM generations WHERE uuid=ANY(%s)", (ids,)).fetchall()}
        if len(rows) != len(ids):
            raise HTTPException(404, "One or more uploaded videos were not found. No testset was created.")
        items = []
        for identifier in ids:
            row = rows[identifier]
            tags = row["tags"]
            items.append({"generation_id": str(identifier), "artifact_id": str(row["artifact_id"]),
                          "tag_index": 0, "set": tags["set"], "labels": tags["labels"],
                          "metadata": tags.get("metadata", {}), "provenance": tags.get("provenance", {}),
                          "annotation_updated_at": row["updated_at"].isoformat()})
        return conn.execute("INSERT INTO testsets(uuid,name,query,items) VALUES(%s,%s,%s,%s) RETURNING *",
                            (payload.request_id, name, Jsonb(query), Jsonb(items))).fetchone()
