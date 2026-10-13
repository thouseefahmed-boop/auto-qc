import json
import shutil
import subprocess
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest

@pytest.fixture
def storage(client, monkeypatch):
    # Import only after client sets TEST_DATABASE_URL's isolated search_path.
    from autoqc import uploads
    calls = []
    state = {"fail": False}

    class StorageClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, data, files):
            calls.append((url, data, files["file"][1].read()))
            request = httpx.Request("POST", url)
            if state["fail"]:
                raise httpx.ConnectError("unavailable", request=request)
            return httpx.Response(200, request=request, json={
                "artifact_id": str(uuid5(NAMESPACE_URL, data["external_id"]))})

    monkeypatch.setattr(uploads.httpx, "Client", StorageClient)
    monkeypatch.setattr(uploads, "inspect_video", lambda _: {"duration_seconds": 1, "width": 64, "height": 64})
    return calls, state


def send(client, content=b"video bytes", filename="clip.mp4"):
    return client.post("/api/videos/upload", files={"file": (filename, content, "video/mp4")})


def test_upload_retries_preserve_reviewed_labels_and_private_storage(client, storage):
    calls, _ = storage
    first = send(client)
    assert first.status_code == 201, first.text
    row = first.json()
    assert row["tags"]["set"] == "unclassified"
    assert set(row["tags"]["labels"].values()) == {None}
    assert row["tags"]["metadata"]["filename"] == "clip.mp4"
    assert calls[0][1]["visibility"] == "PRIVATE"
    assert calls[0][1]["artifact_type"] == "VIDEO"
    assert calls[0][2] == b"video bytes"
    reviewed = client.patch("/api/generations/" + row["uuid"] + "/labels", json={
        "expected_updated_at": row["updated_at"], "labels": {"bad_movement": True}})
    assert reviewed.status_code == 200
    again = send(client, filename="renamed.mp4").json()
    assert again["uuid"] == row["uuid"]
    assert again["tags"]["labels"]["bad_movement"] is True
    assert calls[0][1]["external_id"] == calls[1][1]["external_id"]


def test_uploaded_batch_freezes_order_unknown_labels_and_unique_membership(client, storage, worker_headers):
    first = send(client, b"video one").json()
    second = send(client, b"video two", "second.mov").json()
    request_id = str(uuid4())
    body = {"name": "Uploaded batch", "generation_ids": [second["uuid"], first["uuid"], second["uuid"]],
            "request_id": request_id}
    response = client.post("/api/testsets/from-videos", json=body)
    assert response.status_code == 201, response.text
    testset = response.json()
    assert [item["artifact_id"] for item in testset["items"]] == [second["artifact_id"], first["artifact_id"]]
    assert all(set(item["labels"].values()) == {None} for item in testset["items"])
    assert testset["query"]["source"] == "upload"
    assert client.post("/api/testsets/from-videos", json=body).json()["uuid"] == request_id
    assert client.post("/api/testsets/from-videos", json={**body, "name": "Different"}).status_code == 409
    # Unknown human labels still allow a real benchmark job; they cannot inflate scores.
    mode = next(m for m in client.get("/api/modes").json() if m["name"] == "cosmos4fps")
    recipe = client.post("/api/recipes", json={"name": "upload-" + uuid4().hex, "prompt": "Inspect."}).json()
    run = client.post("/api/benchmarks", json={"mode_ids": [mode["uuid"]], "testset_id": request_id,
                                             "recipe_id": recipe["uuid"]}).json()[0]
    assert run["numbers"]["total"] == 2
    assert run["numbers"]["labels"]["bad_product"]["detection_rate"] is None
    job = client.post("/api/worker/claim", json={"worker_id": "upload-test", "models": ["cosmos4fps"]},
                      headers=worker_headers).json()["job"]
    assert job["uuid"] == run["uuid"]
    assert len(job["items"]) == 2
    assert "labels" not in job["items"][0]
    client.post("/api/benchmarks/" + run["uuid"] + "/cancel")


def test_missing_batch_member_creates_no_partial_testset(client, storage):
    uploaded = send(client, b"existing video").json()
    request_id = str(uuid4())
    response = client.post("/api/testsets/from-videos", json={"name": "Incomplete", "request_id": request_id,
                       "generation_ids": [uploaded["uuid"], str(uuid4())]})
    assert response.status_code == 404
    assert client.get("/api/testsets/" + request_id).status_code == 404


def test_invalid_and_oversized_uploads_do_not_reach_storage(client, storage, monkeypatch):
    calls, _ = storage
    assert send(client, filename="clip.txt").status_code == 422
    assert send(client, b"").status_code == 422
    monkeypatch.setenv("AUTOQC_UPLOAD_MAX_BYTES", "100")
    assert send(client, b"x" * 101).status_code == 413
    # Also exercise the streaming body cap before multipart parsing finishes.
    assert send(client, b"x" * 70000).status_code == 413
    assert not calls


def test_storage_failure_can_be_retried_without_duplicate_records(client, storage):
    _, state = storage
    state["fail"] = True
    assert send(client, b"retry video").status_code == 502
    state["fail"] = False
    first = send(client, b"retry video").json()
    assert send(client, b"retry video").json()["uuid"] == first["uuid"]


def test_real_video_validation_rejects_spoofed_and_audio_only_files(tmp_path):
    from autoqc import uploads
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Requires ffmpeg, installed in the deployment images")
    path = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    assert uploads.inspect_video(path)["width"] == 64
    spoofed = tmp_path / "spoofed.mp4"
    spoofed.write_bytes(b"not a video")
    with pytest.raises(uploads.HTTPException) as exc:
        uploads.inspect_video(spoofed)
    assert exc.value.status_code == 422
    audio = tmp_path / "audio.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", "aac", str(audio)], check=True)
    with pytest.raises(uploads.HTTPException):
        uploads.inspect_video(audio)
