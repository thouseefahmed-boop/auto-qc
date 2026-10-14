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
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
MAX_IMAGE_FILES = 20
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGES_PER_VIDEO_BYTES = 100 * 1024 * 1024


def upload_limits():
    return {"max_files": 100, "max_bytes": int(os.getenv("AUTOQC_UPLOAD_MAX_BYTES", str(500 * 1024 * 1024))),
            "extensions": list(VIDEO_TYPES), "image_extensions": list(IMAGE_TYPES),
            "max_source_images": MAX_IMAGE_FILES, "max_image_bytes": MAX_IMAGE_BYTES,
            "max_source_images_bytes": MAX_IMAGES_PER_VIDEO_BYTES}


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


def inspect_image(path, expected_type):
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height",
            "-of", "json", str(path),
        ], capture_output=True, timeout=20, check=True)
        stream = json.loads(result.stdout)["streams"][0]
        expected_codec = {"image/jpeg": "mjpeg", "image/png": "png", "image/webp": "webp"}[expected_type]
        if stream.get("codec_name") != expected_codec or min(stream.get("width", 0), stream.get("height", 0)) < 1:
            raise ValueError("Image type does not match its file extension")
        return {"width": stream["width"], "height": stream["height"]}
    except FileNotFoundError as exc:
        raise HTTPException(503, "Image uploads are unavailable: the media validator is not installed") from exc
    except (subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
        raise HTTPException(422, "This source image is unreadable. Choose a JPEG, PNG, or WebP image.") from exc


def store_source_image(upload, visibility):
    filename = (upload.filename or "").replace("\\", "/").split("/")[-1][:240]
    suffix = Path(filename).suffix.lower()
    if suffix not in IMAGE_TYPES:
        raise HTTPException(422, "Choose a JPEG, PNG, or WebP source image")
    digest = hashlib.sha256()
    size = 0
    with tempfile.NamedTemporaryFile(suffix=suffix) as staged:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise HTTPException(413, "Each source image must be 25 MB or smaller")
            digest.update(chunk)
            staged.write(chunk)
        if not size:
            raise HTTPException(422, "A source image is empty")
        staged.flush()
        dimensions = inspect_image(staged.name, IMAGE_TYPES[suffix])
        checksum = digest.hexdigest()
        metadata = {"filename": filename, "upload_source": "autoqc_upload",
                    "sha256": checksum, "size_bytes": size, **dimensions}
        staged.seek(0)
        base = os.getenv("ARTIFACTS_BASE", "http://rtx-1.dev.internal:8090/artifacts-service").rstrip("/")
        try:
            with httpx.Client(timeout=httpx.Timeout(120, connect=10)) as client:
                response = client.post(base + "/internal/artifacts/upload-multipart", data={
                    "tenant_id": os.getenv("ARTIFACT_TENANT", "Flipkart"),
                    "external_id": f"autoqc-source-image-{checksum}-{visibility.lower()}", "source": "autoqc_upload",
                    "artifact_type": "IMAGE", "version": "1", "visibility": visibility,
                    "filename": filename, "content_type": IMAGE_TYPES[suffix],
                    "metadata_json": json.dumps(metadata),
                }, files={"file": (filename, staged, IMAGE_TYPES[suffix])})
                response.raise_for_status()
                artifact_id = UUID(response.json()["artifact_id"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(502, "Source-image storage is unavailable. Retry; completed image uploads are retained.") from exc
    base_url = os.getenv("AUTOQC_PUBLIC_BASE_URL", "http://rtx-3.dev.internal:8810").rstrip("/")
    return {"artifact_id": str(artifact_id), "filename": filename, "url": f"{base_url}/api/images/{artifact_id}",
            "content_type": IMAGE_TYPES[suffix], "size_bytes": size, **dimensions}


def store_video(upload, source_images, visibility):
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
        lineage_hash = hashlib.sha256("\n".join(image["artifact_id"] for image in source_images).encode()).hexdigest()
        metadata = {"filename": filename, "product_title": filename, "upload_source": "autoqc_upload",
                    "sha256": checksum, "size_bytes": size, **video_info,
                    "visibility": visibility, "source_images": source_images,
                    "reference_image_urls": [image["url"] for image in source_images]}
        # Upload outside any DB transaction. Content-based idempotency makes a
        # retry after a lost response reuse the artifact rather than store it twice.
        staged.seek(0)
        base = os.getenv("ARTIFACTS_BASE", "http://rtx-1.dev.internal:8090/artifacts-service").rstrip("/")
        try:
            with httpx.Client(timeout=httpx.Timeout(600, connect=10)) as client:
                response = client.post(base + "/internal/artifacts/upload-multipart", data={
                    "tenant_id": os.getenv("ARTIFACT_TENANT", "Flipkart"),
                    "external_id": f"autoqc-video-{checksum}-{lineage_hash}-{visibility.lower()}", "source": "autoqc_upload",
                    "artifact_type": "VIDEO", "version": "1", "visibility": visibility,
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


def store_video_with_sources(video, images, visibility):
    # Validate the full selection before sending any bytes to artifact storage.
    name = (video.filename or "").replace("\\", "/").split("/")[-1][:240]
    suffix = Path(name).suffix.lower()
    if suffix not in VIDEO_TYPES:
        raise HTTPException(422, "Choose an MP4, MOV, or WebM video")
    with tempfile.NamedTemporaryFile(suffix=suffix) as staged:
        size = 0
        video.file.seek(0)
        while chunk := video.file.read(1024 * 1024):
            size += len(chunk)
            if size > upload_limits()["max_bytes"]:
                raise HTTPException(413, "This video exceeds the upload size limit")
            staged.write(chunk)
        if not size:
            raise HTTPException(422, "This video is empty")
        staged.flush()
        inspect_video(staged.name)
    video.file.seek(0)
    total_image_bytes = 0
    for image in images:
        image_name = (image.filename or "").replace("\\", "/").split("/")[-1][:240]
        image_suffix = Path(image_name).suffix.lower()
        if image_suffix not in IMAGE_TYPES:
            raise HTTPException(422, "Choose a JPEG, PNG, or WebP source image")
        if not image.size or image.size > MAX_IMAGE_BYTES:
            raise HTTPException(413 if image.size else 422, "Each source image must be non-empty and 25 MB or smaller")
        total_image_bytes += image.size
        with tempfile.NamedTemporaryFile(suffix=image_suffix) as staged:
            image.file.seek(0)
            while chunk := image.file.read(1024 * 1024):
                staged.write(chunk)
            staged.flush()
            inspect_image(staged.name, IMAGE_TYPES[image_suffix])
        image.file.seek(0)
    if total_image_bytes > MAX_IMAGES_PER_VIDEO_BYTES:
        raise HTTPException(413, "Source images for one video must total 100 MB or less")
    source_images = [store_source_image(image, visibility) for image in images]
    video.visibility = visibility
    return store_video(video, source_images, visibility)


class _UploadTooLarge(MultiPartException):
    def __init__(self):
        super().__init__("Upload exceeds the size limit")


@router.post("/api/videos/upload", status_code=201)
async def upload_video(request: Request):
    # Bound the body during multipart parsing, including chunked requests, so a
    # rejected oversized upload cannot fill the temporary spool before validation.
    max_body = upload_limits()["max_bytes"] + MAX_IMAGES_PER_VIDEO_BYTES + 256 * 1024
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
        async with limited.form(max_files=MAX_IMAGE_FILES + 1, max_fields=1) as form:
            videos = form.getlist("file")
            images = form.getlist("source_images")
            visibility = form.get("visibility", "PRIVATE")
            if len(videos) != 1 or not isinstance(videos[0], UploadFile):
                raise HTTPException(422, "Choose one video per upload request")
            if not images or len(images) > MAX_IMAGE_FILES or any(not isinstance(image, UploadFile) for image in images):
                raise HTTPException(422, "Attach 1 to 20 source images to every video")
            if visibility not in {"PRIVATE", "PUBLIC"}:
                raise HTTPException(422, "Choose PRIVATE or PUBLIC storage visibility")
            if sum(getattr(image, "size", 0) or 0 for image in images) > MAX_IMAGES_PER_VIDEO_BYTES:
                raise HTTPException(413, "Source images for one video must total 100 MB or less")
            return await run_in_threadpool(store_video_with_sources, videos[0], images, visibility)
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
            source_images = tags.get("metadata", {}).get("source_images", [])
            if not source_images:
                raise HTTPException(422, "Every video in an uploaded testset must have at least one source image")
            items.append({"generation_id": str(identifier), "artifact_id": str(row["artifact_id"]),
                          "tag_index": 0, "set": tags["set"], "labels": tags["labels"],
                          "metadata": tags.get("metadata", {}), "provenance": tags.get("provenance", {}),
                          "annotation_updated_at": row["updated_at"].isoformat()})
        return conn.execute("INSERT INTO testsets(uuid,name,query,items) VALUES(%s,%s,%s,%s) RETURNING *",
                            (payload.request_id, name, Jsonb(query), Jsonb(items))).fetchone()
