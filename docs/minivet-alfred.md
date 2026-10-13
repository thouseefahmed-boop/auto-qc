# AutoQC with MiniVET Alfred and Cosmos 3

The benchmark path is AutoQC's authenticated pull worker → MiniVET Alfred
`CosmosServer` → the drivetrain gateway → Cosmos 3. The existing UI, PostgreSQL
database, artifact-service media URLs, worker leases and per-video result saves
remain the benchmark source of truth. No second queue or blob store is needed.

Install the optional integration from the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[minivet,test]'
.venv/bin/pytest -q
```

The dependency pins `alfred_src` from MinivetAI/minivet-architecture's newArch
commit `bcc76349dad3b82fb8278be5c27ea72056576d0c`. Upstream MinivetAI/alfred
0.4.0 does not export the architecture's `CosmosServer`; installing an unrelated
PyPI package named alfred will not work. Private-repository Git access is needed.

The live served model verified on 13 October 2026 is `Cosmos3-Super`, exposed at
`http://prod.dev.internal/cosmos/v1/models`. The API's frozen worker settings
choose the endpoint, served model and frame sampling for each benchmark. The
adapter accepts endpoints with or without a trailing `/v1`, sends all product
references, preserves extensionless video URLs, and puts media before text for
Cosmos. Cosmos defaults to 4 FPS and a 900-second request timeout; Qwen uses its
existing sampling handoff and a 200-second timeout. `AUTOQC_LLM_TIMEOUT` can
override either deadline. Alfred retries are disabled.

## Run the repository on rtx-3, GPU index 2

The deployed app is at `http://rtx-3.dev.internal:8810`, with source under
`/home/pragya.mishra/autoqc-minivet`. It has its own persistent PostgreSQL volume,
API/UI container and Cosmos worker. The worker container exposes only physical
GPU index 2, identified live by UUID. It calls the supplied Cosmos gateway for
inference; the gateway controls the model's actual GPU placement.

To reproduce the deployment, stage the pinned architecture's `alfred_src`
directory in `_build/alfred` and run on rtx-3:

```sh
python3 deploy/minivet.py
```

No existing `.env` or other deployment's worker token is needed. The installer
creates fresh internal database/worker credentials in a private mode-0600
`.minivet-runtime.json` and preserves them on later deployments. The file is
excluded from Git and the image. Do not delete it while retaining the database
volume. PostgreSQL is reachable only on the app's Docker network.

The worker claims `cosmos4fps` benchmarks from this app's API. Benchmark video
URLs use `http://rtx-3.dev.internal:8810/api/media/<artifact-id>` so the remote
model can fetch them. Artifacts stay in MiniVET's existing artifact service.
The app does not consume the shared MiniVET `LLM` queue. The deployment creates
an independent database; it does not migrate the older H200 benchmark history.

Inspect services with `docker ps --filter name=autoqc-minivet` and worker logs
with `docker logs autoqc-minivet-cosmos-worker-1`. To stop this app without
removing its database, use `docker stop autoqc-minivet-cosmos-worker-1
autoqc-minivet-api-1 autoqc-minivet-db-1`.

## A dedicated Cosmos model on rtx-3 GPU index 2

That is a separate model deployment. GPU 2 was occupied by
`qwen-image-21-turbo-turbo-1` (about 23 GiB VRAM) during inspection. Verify the
current model-serving image, checkpoint and memory budget before deploying a
second model; do not stop the image service implicitly. An exact GPU deployment
must expose only UUID `GPU-c909f63b-bce4-bdbc-37eb-84ab96e08401` to its model
container, then register its health-checked service in the existing router.
The UUID must be checked live before use. Point future AutoQC worker settings
to the registered gateway route, retaining `Cosmos3-Super` as the served name.
Existing frozen benchmark snapshots retain their original endpoint.

