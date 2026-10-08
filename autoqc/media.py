"""Range-aware S3 playback; videos are never bulk-downloaded to disk."""
import os
import ssl
import time
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse

_urls: dict[str, tuple[float, str]] = {}


def presigned_url(artifact_id: UUID) -> str:
    key = str(artifact_id)
    cached = _urls.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    base = os.getenv("ARTIFACTS_BASE", "http://rtx-1.dev.internal:8090/artifacts-service").rstrip("/")
    try:
        response = httpx.get(f"{base}/internal/artifacts/{artifact_id}/download",
                             params={"tenant_id": os.getenv("ARTIFACT_TENANT", "Flipkart"),
                                     "delivery_mode": "presigned"}, timeout=20)
        response.raise_for_status()
        url = response.json()["download_url"]
        if urlparse(url).scheme not in {"http", "https"}:
            raise ValueError("Invalid artifact delivery URL")
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(502, "Artifact service could not resolve this video") from exc
    if len(_urls) >= 1000:
        _urls.clear()
    _urls[key] = (time.monotonic() + 120, url)
    return url


def stream_video(artifact_id: UUID, request: Request):
    url = presigned_url(artifact_id)
    verify = os.getenv("MEDIA_VERIFY_TLS", "true").lower() != "false"
    ca = os.getenv("MEDIA_CA_FILE")
    tls = ssl.create_default_context(cafile=ca) if ca else verify
    client = httpx.Client(verify=tls, follow_redirects=True, timeout=60)
    headers = {"Range": request.headers["range"]} if "range" in request.headers else {}
    try:
        upstream = client.send(client.build_request(request.method, url, headers=headers), stream=True)
    except httpx.HTTPError as exc:
        client.close()
        raise HTTPException(502, "Video storage is unreachable") from exc
    if upstream.status_code not in {200, 206, 416}:
        upstream.close()
        client.close()
        _urls.pop(str(artifact_id), None)
        raise HTTPException(502, "Video storage returned an error; retry playback")
    forwarded = {k: v for k, v in upstream.headers.items() if k.lower() in
                 {"content-type", "content-length", "content-range", "accept-ranges", "etag"}}
    forwarded["Cache-Control"] = "private, max-age=60"
    if request.method == "HEAD":
        status = upstream.status_code
        upstream.close()
        client.close()
        return Response(status_code=status, headers=forwarded)

    def chunks():
        try:
            yield from upstream.iter_raw(128 * 1024)
        finally:
            upstream.close()
            client.close()

    return StreamingResponse(chunks(), status_code=upstream.status_code, headers=forwarded)

