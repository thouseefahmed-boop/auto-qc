import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from starlette.middleware.base import BaseHTTPMiddleware

from .db import initialize, pool
from .media import stream_video
from .metrics import metrics
from .models import (LABELS, BenchmarkIn, ClaimIn, FailIn, GenerationIn, LeaseIn,
                     ModeConfig, ModeIn, Recipe, ResultIn, ReviewIn, TestsetIn, set_for)
from .selection import SelectionError, select_items


@asynccontextmanager
async def lifespan(app):
    initialize()
    with pool.connection() as conn:
        for name, model in (("cosmos4fps", "cosmos"), ("qwenA3b4fps", "qwen-A3B")):
            conn.execute("INSERT INTO modes(uuid,name,config) VALUES(%s,%s,%s) ON CONFLICT(name) DO NOTHING",
                         (uuid4(), name, Jsonb(ModeConfig(model=model).model_dump())))
    yield
    pool.close()


app = FastAPI(title="AutoQC", version="0.1.0", lifespan=lifespan)


class SameOriginWrites(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Block browser cross-origin writes to a shared internal tool. Worker
        # clients have no browser Origin and use their separate bearer token.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            from urllib.parse import urlparse
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin writes are not allowed"}, status_code=403)
        result = await call_next(request)
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Referrer-Policy"] = "same-origin"
        return result


app.add_middleware(SameOriginWrites)


@app.exception_handler(UniqueViolation)
async def duplicate_handler(request, exc):
    return JSONResponse({"detail": "This name or artifact already exists"}, status_code=409)


def get_row(conn, table, identifier, lock=False):
    # Table names are developer-controlled, never user-provided.
    row = conn.execute(f"SELECT * FROM {table} WHERE uuid=%s" + (" FOR UPDATE" if lock else ""),
                       (identifier,)).fetchone()
    if not row:
        raise HTTPException(404, f"{table} record not found")
    return row


@app.get("/api/health")
def health():
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "database": "postgresql", "workers": "external"}


@app.get("/api/stats")
def stats():
    with pool.connection() as conn:
        total = conn.execute("SELECT count(*) AS total FROM generations").fetchone()["total"]
        sets = conn.execute("SELECT tags->>'set' AS name, count(*) AS count FROM generations GROUP BY 1").fetchall()
        labels = {label: conn.execute("SELECT count(*) AS count FROM generations WHERE tags @> %s",
                                      (Jsonb({"labels": {label: True}}),)).fetchone()["count"] for label in LABELS}
        counts = {table: conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
                  for table in ("testsets", "modes", "benchmarks")}
        status = conn.execute("SELECT status, count(*) AS count FROM benchmarks GROUP BY status").fetchall()
    return {"generations": total, "sets": {s["name"]: s["count"] for s in sets}, "labels": labels,
            **counts, "benchmark_status": {s["status"]: s["count"] for s in status},
            "workers_configured": bool(os.getenv("AUTOQC_WORKER_TOKEN")),
            "label_policy": "Legacy nudity approvals are exposure-policy positives, not necessarily explicit nudity."}


@app.get("/api/generations")
def generations(set: str | None = None, label: str | None = None, search: str = "",
                limit: int = Query(24, ge=1, le=200), offset: int = Query(0, ge=0)):
    clauses, params = [], []
    if set:
        if set not in {"normal", "adversarial", "unclassified"}:
            raise HTTPException(422, "Invalid set")
        clauses.append("tags @> %s")
        params.append(Jsonb({"set": set}))
    if label:
        if label not in LABELS:
            raise HTTPException(422, "Invalid label")
        clauses.append("tags @> %s")
        params.append(Jsonb({"labels": {label: True}}))
    if search:
        clauses.append("(artifact_id::text ILIKE %s OR tags->'metadata'->>'product_title' ILIKE %s OR tags->'metadata'->>'item_id' ILIKE %s)")
        params.extend([f"%{search[:200]}%"] * 3)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with pool.connection() as conn:
        total = conn.execute("SELECT count(*) AS n FROM generations" + where, params).fetchone()["n"]
        rows = conn.execute("SELECT * FROM generations" + where + " ORDER BY created_at DESC,uuid LIMIT %s OFFSET %s",
                            params + [limit, offset]).fetchall()
    return {"items": rows, "total": total}


@app.post("/api/generations", status_code=201)
def create_generation(payload: GenerationIn):
    labels = payload.labels.model_dump()
    tags = {"set": set_for(labels), "labels": labels, "metadata": payload.metadata,
            "provenance": {"manual": {"at": datetime.now(timezone.utc).isoformat(), "note": payload.note}}}
    with pool.connection() as conn:
        return conn.execute("INSERT INTO generations(uuid,artifact_id,tags) VALUES(%s,%s,%s) RETURNING *",
                            (uuid4(), payload.artifact_id, Jsonb(tags))).fetchone()


@app.get("/api/generations/{identifier}")
def generation(identifier: UUID):
    with pool.connection() as conn:
        return get_row(conn, "generations", identifier)


@app.patch("/api/generations/{identifier}/labels")
def review_generation(identifier: UUID, payload: ReviewIn):
    with pool.connection() as conn:
        row = get_row(conn, "generations", identifier, lock=True)
        try:
            expected = datetime.fromisoformat(payload.expected_updated_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, "Invalid expected_updated_at")
        if expected != row["updated_at"]:
            raise HTTPException(409, "Someone changed these labels. Refresh before saving.")
        tags = row["tags"]
        history = tags.setdefault("history", [])
        history.append({"labels": tags["labels"], "at": row["updated_at"].isoformat(),
                        "provenance": tags.get("provenance", {})})
        tags["labels"] = payload.labels.model_dump()
        tags["set"] = set_for(tags["labels"])
        tags["provenance"] = {"manual": {"at": datetime.now(timezone.utc).isoformat(), "note": payload.note}}
        return conn.execute("UPDATE generations SET tags=%s,updated_at=clock_timestamp() WHERE uuid=%s RETURNING *",
                            (Jsonb(tags), identifier)).fetchone()


def materialize(conn, recipe):
    rows = conn.execute("SELECT * FROM generations ORDER BY uuid").fetchall()
    try:
        return select_items(rows, recipe)
    except SelectionError as exc:
        raise HTTPException(409, {"message": str(exc), "availability": exc.availability})


@app.post("/api/testsets/preview")
def preview(payload: Recipe):
    with pool.connection() as conn:
        items, availability = materialize(conn, payload)
    return {"items": items, "availability": availability, "total": len(items)}


@app.get("/api/testsets")
def testsets():
    with pool.connection() as conn:
        return conn.execute("SELECT uuid,name,query,jsonb_array_length(items) AS size,created_at FROM testsets ORDER BY created_at DESC").fetchall()


@app.post("/api/testsets", status_code=201)
def create_testset(payload: TestsetIn):
    with pool.connection() as conn:
        items, _ = materialize(conn, payload.query)
        return conn.execute("INSERT INTO testsets(uuid,name,query,items) VALUES(%s,%s,%s,%s) RETURNING *",
                            (uuid4(), payload.name.strip(), Jsonb(payload.query.model_dump()), Jsonb(items))).fetchone()


@app.get("/api/testsets/{identifier}")
def testset(identifier: UUID):
    with pool.connection() as conn:
        return get_row(conn, "testsets", identifier)


@app.get("/api/modes")
def modes():
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM modes ORDER BY name").fetchall()


@app.post("/api/modes", status_code=201)
def create_mode(payload: ModeIn):
    with pool.connection() as conn:
        return conn.execute("INSERT INTO modes(uuid,name,config) VALUES(%s,%s,%s) RETURNING *",
                            (uuid4(), payload.name.strip(), Jsonb(payload.config.model_dump()))).fetchone()


@app.put("/api/modes/{identifier}")
def update_mode(identifier: UUID, payload: ModeIn):
    with pool.connection() as conn:
        get_row(conn, "modes", identifier, lock=True)
        return conn.execute("UPDATE modes SET name=%s,config=%s,updated_at=clock_timestamp() WHERE uuid=%s RETURNING *",
                            (payload.name.strip(), Jsonb(payload.config.model_dump()), identifier)).fetchone()


@app.post("/api/benchmarks", status_code=201)
def start_benchmarks(payload: BenchmarkIn):
    if len(set(payload.mode_ids)) != len(payload.mode_ids):
        raise HTTPException(422, "Select each mode once")
    with pool.connection() as conn:
        test = get_row(conn, "testsets", payload.testset_id, lock=True)
        runs = []
        for identifier in payload.mode_ids:
            mode = get_row(conn, "modes", identifier)
            snapshot = {"mode_name": mode["name"], "config": mode["config"],
                        "testset_name": test["name"], "query": test["query"], "protocol_version": 1}
            runs.append(conn.execute("INSERT INTO benchmarks(uuid,mode_id,testset_id,config_snapshot,numbers) VALUES(%s,%s,%s,%s,%s) RETURNING *",
                                     (uuid4(), identifier, test["uuid"], Jsonb(snapshot), Jsonb(metrics(test["items"], {})))).fetchone())
    return runs


@app.get("/api/benchmarks")
def benchmarks(limit: int = Query(100, ge=1, le=500), testset_id: UUID | None = None):
    with pool.connection() as conn:
        where = " WHERE testset_id=%s" if testset_id else ""
        return conn.execute("SELECT uuid,mode_id,testset_id,status,numbers,config_snapshot,worker_id,error,created_at,started_at,finished_at FROM benchmarks" + where + " ORDER BY created_at DESC LIMIT %s",
                            ([testset_id] if testset_id else []) + [limit]).fetchall()


@app.get("/api/benchmarks/{identifier}")
def benchmark(identifier: UUID):
    with pool.connection() as conn:
        row = get_row(conn, "benchmarks", identifier)
        # Never expose worker lease credentials to browser clients.
        row.pop("lease_token", None)
        row["items"] = get_row(conn, "testsets", row["testset_id"])["items"]
        return row


@app.post("/api/benchmarks/{identifier}/cancel")
def cancel(identifier: UUID):
    with pool.connection() as conn:
        row = get_row(conn, "benchmarks", identifier, lock=True)
        if row["status"] not in {"queued", "running"}:
            raise HTTPException(409, "This run is already terminal")
        conn.execute("UPDATE benchmarks SET status='cancelled',finished_at=now(),lease_token=NULL,lease_expires_at=NULL WHERE uuid=%s", (identifier,))
    return {"status": "cancelled", "predictions_preserved": True}


def worker_auth(authorization: str | None = Header(default=None)):
    token = os.getenv("AUTOQC_WORKER_TOKEN", "")
    if not token:
        raise HTTPException(503, "Worker integration has not been configured")
    if not secrets.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(401, "Invalid worker credentials")


@app.post("/api/worker/claim", dependencies=[Depends(worker_auth)])
def claim(payload: ClaimIn, request: Request):
    with pool.connection() as conn:
        row = conn.execute("""SELECT * FROM benchmarks
            WHERE (status='queued' OR (status='running' AND lease_expires_at < now()))
            AND config_snapshot->'config'->>'worker_pool'=%s
            AND config_snapshot->'config'->>'model'=ANY(%s)
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""",
                           (payload.worker_pool, payload.models)).fetchone()
        if not row:
            return {"job": None}
        token = uuid4()
        conn.execute("""UPDATE benchmarks SET status='running',worker_id=%s,lease_token=%s,
            lease_expires_at=now() + (%s * interval '1 second'),started_at=coalesce(started_at,now()) WHERE uuid=%s""",
                     (payload.worker_id, token, payload.lease_seconds, row["uuid"]))
        items = get_row(conn, "testsets", row["testset_id"])["items"]
        # Ground truth is not sent to the model worker: avoid label leakage.
        pending = [{"artifact_id": item["artifact_id"], "metadata": item.get("metadata", {}),
                    "video_url": str(request.base_url).rstrip("/") + f'/api/media/{item["artifact_id"]}'}
                   for item in items if item["artifact_id"] not in row["predictions"]]
    return {"job": {"uuid": row["uuid"], "lease_token": token, "lease_seconds": payload.lease_seconds,
                    "config": row["config_snapshot"]["config"], "items": pending,
                    "already_processed": len(row["predictions"])}}


def leased(conn, identifier, token):
    row = get_row(conn, "benchmarks", identifier, lock=True)
    if row["status"] != "running" or row["lease_token"] != token:
        raise HTTPException(409, "This worker no longer owns the run")
    if row["lease_expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(409, "Lease expired. Claim the run again before submitting.")
    return row


@app.post("/api/worker/{identifier}/heartbeat", dependencies=[Depends(worker_auth)])
def heartbeat(identifier: UUID, payload: LeaseIn):
    with pool.connection() as conn:
        leased(conn, identifier, payload.lease_token)
        conn.execute("UPDATE benchmarks SET lease_expires_at=now()+interval '5 minutes' WHERE uuid=%s", (identifier,))
    return {"status": "ok", "lease_seconds": 300}


@app.post("/api/worker/{identifier}/result", dependencies=[Depends(worker_auth)])
def submit_result(identifier: UUID, payload: ResultIn):
    with pool.connection() as conn:
        row = leased(conn, identifier, payload.lease_token)
        items = get_row(conn, "testsets", row["testset_id"])["items"]
        key = str(payload.artifact_id)
        if key not in {i["artifact_id"] for i in items}:
            raise HTTPException(422, "This video is not in the frozen testset")
        result = {"labels": payload.labels.model_dump() if payload.labels else None,
                  "error": payload.error, "evidence": payload.evidence}
        old = row["predictions"].get(key)
        if old:
            if any(old.get(k) != v for k, v in result.items()):
                raise HTTPException(409, "A different result is already stored for this video")
            return {"status": "already_saved", "processed": len(row["predictions"])}
        result["saved_at"] = datetime.now(timezone.utc).isoformat()
        predictions = {**row["predictions"], key: result}
        numbers = metrics(items, predictions)
        conn.execute("UPDATE benchmarks SET predictions=%s,numbers=%s,lease_expires_at=now()+interval '5 minutes' WHERE uuid=%s",
                     (Jsonb(predictions), Jsonb(numbers), identifier))
    return {"status": "saved", "processed": len(predictions), "total": len(items)}


@app.post("/api/worker/{identifier}/complete", dependencies=[Depends(worker_auth)])
def complete(identifier: UUID, payload: LeaseIn):
    with pool.connection() as conn:
        row = leased(conn, identifier, payload.lease_token)
        items = get_row(conn, "testsets", row["testset_id"])["items"]
        if len(row["predictions"]) != len(items):
            raise HTTPException(409, "Not every video has a result or explicit inference error")
        conn.execute("UPDATE benchmarks SET status='completed',numbers=%s,finished_at=now(),lease_token=NULL,lease_expires_at=NULL WHERE uuid=%s",
                     (Jsonb(metrics(items, row["predictions"])), identifier))
    return {"status": "completed"}


@app.get("/api/export")
def export():
    with pool.connection() as conn:
        data = {table: conn.execute(f"SELECT * FROM {table} ORDER BY created_at,uuid").fetchall()
                for table in ("generations", "testsets", "modes", "benchmarks")}
    for row in data["benchmarks"]:
        row.pop("lease_token", None)
    return JSONResponse(jsonable_encoder(data), headers={"Content-Disposition": 'attachment; filename="autoqc-export.json"'})


@app.post("/api/worker/{identifier}/fail", dependencies=[Depends(worker_auth)])
def fail(identifier: UUID, payload: FailIn):
    with pool.connection() as conn:
        leased(conn, identifier, payload.lease_token)
        conn.execute("UPDATE benchmarks SET status='failed',error=%s,finished_at=now(),lease_token=NULL,lease_expires_at=NULL WHERE uuid=%s",
                     (payload.error, identifier))
    return {"status": "failed", "predictions_preserved": True}


@app.get("/api/media/{artifact_id}")
@app.head("/api/media/{artifact_id}", include_in_schema=False)
def media(artifact_id: UUID, request: Request):
    with pool.connection() as conn:
        if not conn.execute("SELECT 1 FROM generations WHERE artifact_id=%s", (artifact_id,)).fetchone():
            raise HTTPException(404, "Unknown artifact")
    return stream_video(artifact_id, request)


app.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="ui")
