import json
from uuid import uuid4

from autoqc.models import LABELS


def add(client, **labels):
    response = client.post("/api/generations", json={"artifact_id": str(uuid4()), "labels": labels})
    assert response.status_code == 201, response.text
    return response.json()


def test_application_tables(client):
    from autoqc.db import pool
    with pool.connection() as conn:
        names = {r["table_name"] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")}
    assert names == {"generations", "testsets", "modes", "recipes", "benchmarks"}


def test_ui_and_docs(client):
    assert client.get("/").status_code == 200
    assert "AutoQC" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_duplicate_and_unknown_handling(client):
    record = add(client, nudity=True)
    assert record["tags"]["labels"]["bad_product"] is None
    assert record["tags"]["set"] == "adversarial"
    response = client.post("/api/generations", json={"artifact_id": record["artifact_id"], "labels": {}})
    assert response.status_code == 409
    assert client.get("/api/generations?label=nudity").json()["total"] >= 1
    assert client.get("/api/generations?label=not_real").status_code == 422


def test_review_optimistic_lock_and_normal(client):
    record = add(client)
    body = {"labels": dict.fromkeys(LABELS, False), "expected_updated_at": record["updated_at"]}
    response = client.patch(f'/api/generations/{record["uuid"]}/labels', json=body)
    assert response.status_code == 200 and response.json()["tags"]["set"] == "normal"
    assert len(response.json()["tags"]["history"]) == 1
    assert client.patch(f'/api/generations/{record["uuid"]}/labels', json=body).status_code == 409


def test_cross_origin_write_blocked(client):
    response = client.post("/api/generations", headers={"Origin": "https://malicious.example"},
                           json={"artifact_id": str(uuid4()), "labels": {}})
    assert response.status_code == 403


def make_run(client):
    add(client, bad_movement=True)
    test = client.post("/api/testsets", json={"name": "Test run", "query": {"tags": [{"key": "adversarial", "value": "bad_movement", "count": 1}]}})
    assert test.status_code == 201, test.text
    test = test.json()
    mode = client.post("/api/modes", json={"name": "test-" + uuid4().hex}).json()
    recipe = client.post("/api/recipes", json={"name": "recipe-" + uuid4().hex, "prompt": "Return labels."}).json()
    result = client.post("/api/benchmarks", json={"mode_ids": [mode["uuid"]], "testset_id": test["uuid"], "recipe_id": recipe["uuid"]})
    assert result.status_code == 201, result.text
    return result.json()[0], test, mode


def test_snapshot_and_worker_lifecycle(client, worker_headers):
    run, test, mode = make_run(client)
    claim = client.post("/api/worker/claim", headers=worker_headers,
                        json={"worker_id": "test", "models": ["unit-test-model"]}).json()["job"]
    assert claim["uuid"] == run["uuid"]
    assert "labels" not in claim["items"][0] and "set" not in claim["items"][0]
    artifact = test["items"][0]["artifact_id"]
    generation_id = test["items"][0]["generation_id"]
    generation = client.get("/api/generations/" + generation_id).json()
    client.patch("/api/generations/"+generation_id+"/labels", json={"labels": dict.fromkeys(LABELS, False), "expected_updated_at": generation["updated_at"]})
    assert client.get("/api/testsets/"+test["uuid"]).json()["items"][0]["labels"]["bad_movement"] is True
    client.put("/api/modes/"+mode["uuid"], json={"name": mode["name"] + "-renamed"})
    assert client.get("/api/benchmarks/"+run["uuid"]).json()["config_snapshot"]["recipe_prompt"] == "Return labels."
    base = "/api/worker/" + run["uuid"]
    body = {"lease_token": claim["lease_token"], "artifact_id": artifact, "labels": {"bad_movement": True}}
    assert client.post(base+"/complete", headers=worker_headers, json={"lease_token": claim["lease_token"]}).status_code == 409
    assert client.post(base+"/result", headers=worker_headers, json=body).status_code == 200
    assert client.post(base+"/result", headers=worker_headers, json=body).json()["status"] == "already_saved"
    assert client.post(base+"/result", headers=worker_headers, json={**body, "labels": {"bad_movement": False}}).status_code == 409
    assert client.post(base+"/complete", headers=worker_headers, json={"lease_token": claim["lease_token"]}).status_code == 200
    result = client.get("/api/benchmarks/"+run["uuid"]).json()
    assert result["numbers"]["labels"]["bad_movement"]["tp"] == 1
    assert "lease_token" not in result
    assert result["status"] == "completed"


def test_worker_auth_and_reclaim(client, worker_headers):
    run, _, _ = make_run(client)
    body = {"worker_id": "one", "models": ["unit-test-model"]}
    assert client.post("/api/worker/claim", json=body).status_code == 401
    first = client.post("/api/worker/claim", json=body, headers=worker_headers).json()["job"]
    from autoqc.db import pool
    with pool.connection() as conn:
        conn.execute("UPDATE benchmarks SET lease_expires_at=now()-interval '1 second' WHERE uuid=%s", (run["uuid"],))
    second = client.post("/api/worker/claim", json={**body, "worker_id": "two"}, headers=worker_headers).json()["job"]
    assert second["uuid"] == first["uuid"] and second["lease_token"] != first["lease_token"]
    assert client.post(f'/api/worker/{run["uuid"]}/heartbeat', headers=worker_headers, json={"lease_token": first["lease_token"]}).status_code == 409
    assert client.post(f'/api/benchmarks/{run["uuid"]}/cancel').status_code == 200
    assert client.post(f'/api/worker/{run["uuid"]}/heartbeat', headers=worker_headers, json={"lease_token": second["lease_token"]}).status_code == 409


def test_import_idempotent_dedup_and_no_fake_normals(client, tmp_path):
    from autoqc.import_reviews import import_source
    root = tmp_path / "source"
    (root / "review_ui").mkdir(parents=True)
    identifier = str(uuid4())
    (root / "review_ui/reviews.json").write_text(json.dumps({
        f"nudity:{identifier}": {"decision": "approved", "updated_at": "2026-01-01T00:00:00+00:00"},
        f"bad_product:{identifier}": {"decision": "approved", "updated_at": "2026-01-01T00:00:00+00:00"},
        f"bad_movement:{uuid4()}": {"decision": "rejected", "updated_at": "2026-01-01T00:00:00+00:00"}}))
    first = import_source(root)
    second = import_source(root)
    assert first["inserted"] == 1 and second["unchanged"] == 1
    result = client.get("/api/generations?search="+identifier).json()["items"]
    assert len(result) == 1
    assert result[0]["tags"]["labels"]["bad_product"] is True
    assert result[0]["tags"]["labels"]["nudity"] is True
    assert result[0]["tags"]["labels"]["bad_movement"] is None
    assert result[0]["tags"]["set"] == "adversarial"


def test_worker_fail_preserves_results(client, worker_headers):
    run, test, _ = make_run(client)
    claim = client.post("/api/worker/claim", json={"worker_id": "fail-test", "models": ["unit-test-model"]}, headers=worker_headers).json()["job"]
    base = "/api/worker/" + run["uuid"]
    body = {"lease_token": claim["lease_token"], "artifact_id": test["items"][0]["artifact_id"], "error": "Video decode failed"}
    assert client.post(base+"/result", json=body, headers=worker_headers).status_code == 200
    assert client.post(base+"/fail", json={"lease_token": claim["lease_token"], "error": "Model unavailable"}, headers=worker_headers).status_code == 200
    result = client.get("/api/benchmarks/"+run["uuid"]).json()
    assert result["status"] == "failed" and len(result["predictions"]) == 1
    assert result["numbers"]["inference_errors"] == 1


def test_worker_strings_are_not_booleans(client):
    response = client.post("/api/generations", json={"artifact_id": str(uuid4()), "labels": {"nudity": "false"}})
    assert response.status_code == 422


def test_parallel_claims_cannot_double_assign(client, worker_headers):
    from concurrent.futures import ThreadPoolExecutor
    first, _, _ = make_run(client)
    second, _, _ = make_run(client)

    def take(n):
        return client.post("/api/worker/claim", json={"worker_id": f"parallel-{n}", "models": ["unit-test-model"]}, headers=worker_headers).json()["job"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = list(executor.map(take, (1, 2)))
    assert {job["uuid"] for job in jobs} == {first["uuid"], second["uuid"]}
    assert take(3) is None
    for job in jobs:
        client.post(f'/api/benchmarks/{job["uuid"]}/cancel')
