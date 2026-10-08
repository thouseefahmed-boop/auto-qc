"""Reference client for your future workers; no fake predictions or models.

Implement infer(item, config) in a Python module and provide module:function.
The adapter returns {"labels": {...}, "evidence": {...}}. Exceptions are stored
as explicit inference errors. Video URLs are streamed artifact URLs; reference
images/product context are in item["metadata"]. Human labels are never supplied.
"""
import argparse
import importlib
import os
import threading
import time

import httpx
from dotenv import load_dotenv


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--adapter", required=True, help="Your Python module:function")
    p.add_argument("--models", nargs="+", required=True)
    p.add_argument("--worker-id", default="autoqc-worker")
    p.add_argument("--pool", default="default")
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    token = os.environ["AUTOQC_WORKER_TOKEN"]
    module, function = args.adapter.split(":", 1)
    infer = getattr(importlib.import_module(module), function)
    with httpx.Client(base_url=args.url.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=60) as client:
        def post(path, payload):
            # Retrying a saved result is safe: the server verifies identity.
            for attempt in range(5):
                try:
                    r = client.post(path, json=payload)
                    r.raise_for_status()
                    return r.json()
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                        raise
                    if attempt == 4:
                        raise
                    time.sleep(min(2 ** attempt, 10))

        while True:
            claim = post("/api/worker/claim", {"worker_id": args.worker_id, "worker_pool": args.pool, "models": args.models})
            job = claim["job"]
            if not job:
                if args.once:
                    return
                time.sleep(10)
                continue
            lease = {"lease_token": job["lease_token"]}
            prefix = f'/api/worker/{job["uuid"]}'
            stop = threading.Event()
            lease_failed = threading.Event()

            def heartbeat():
                while not stop.wait(60):
                    try:
                        post(prefix + "/heartbeat", lease)
                    except Exception:
                        lease_failed.set()
                        return

            thread = threading.Thread(target=heartbeat, daemon=True)
            thread.start()
            try:
                for item in job["items"]:
                    if lease_failed.is_set():
                        raise RuntimeError("Worker lease lost; stop and reclaim before continuing")
                    try:
                        result = infer(item, job["config"])
                    except Exception as exc:
                        # Do not leak API keys or full remote responses in errors.
                        result = {"error": f"Adapter inference failed ({type(exc).__name__}); inspect worker logs"}
                    post(prefix + "/result", {**lease, "artifact_id": item["artifact_id"], **result})
                post(prefix + "/complete", lease)
                print(f'Completed {job["uuid"]}', flush=True)
            finally:
                stop.set()
                thread.join(timeout=5)
            if args.once:
                return


if __name__ == "__main__":
    main()
