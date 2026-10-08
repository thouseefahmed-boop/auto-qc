# AutoQC

A shared video benchmark workbench built with FastAPI, PostgreSQL, and a browser UI. Model workers are connected later; the app never invents predictions or benchmark scores.

## Running deployment

- UI: **http://10.12.46.7:8810** (internal network/VPN).
- Interactive API docs: http://10.12.46.7:8810/docs.
- Mac source: `/Users/thouseef.ahmed/autoqc`.
- H200 source/runtime: `/home/thouseef.ahmed/autoqc` on `h200-1`.
- Dedicated PostgreSQL: `127.0.0.1:55432`, database `autoqc`. It is not the source `video_generation` database.
- Credentials: private `.env` on H200; not committed, served, or printed by the app.

## What this solves

We have human-approved lifestyle videos with bad-product, bad-movement, and exposure concerns. We need to combine those with reviewed normal videos, evaluate Cosmos and Qwen A3B on the same fixed videos, and inspect how many human-confirmed issues each model catches.

The app separates three things:

1. **Human labels** live in `generations.tags`.
2. **A frozen selection** lives in `testsets.items`, alongside its dataset query.
3. **A named prompt** lives in `recipes`.
4. **Model predictions and numbers** live in each `benchmarks` run.

Editing human labels or model settings later does not change old testsets or old run configurations. There are no Tika review tables, product tables, entity tables, video-tag taxonomies, embeddings, or simulated inference in this project.

## The five tables

### 1. `generations` — the video bank

One row is one unique video artifact. `uuid` is our database row ID; `artifact_id` identifies the video in the existing artifact service. They are intentionally separate.

| Column | PostgreSQL type | Meaning |
| --- | --- | --- |
| `uuid` | `uuid`, primary key | AutoQC generation row ID |
| `artifact_id` | `uuid`, unique, not null | Existing video artifact ID |
| `tags` | `jsonb`, not null | Set, human labels, product reference metadata, and provenance |
| `created_at` | `timestamptz` | Added to AutoQC |
| `updated_at` | `timestamptz` | Latest annotation change; used to detect conflicting saves |

Example tags:

```json
{
  "set": "adversarial",
  "labels": {
    "bad_product": true,
    "bad_movement": null,
    "nudity": null
  },
  "metadata": {
    "product_title": "Cotton saree",
    "product_id": "source-product-id",
    "item_id": "ITM123",
    "image_url": "https://example.com/reference-front.jpg",
    "reference_image_urls": [
      "https://example.com/reference-front.jpg",
      "https://example.com/reference-back.jpg"
    ]
  },
  "provenance": {
    "bad_product": {
      "source": "thumbnail_queue_reviews",
      "decision": "approved",
      "updated_at": "2026-10-06T12:00:00+00:00"
    }
  }
}
```

The three human label states are important:

- `true`: the issue is confirmed present.
- `false`: someone explicitly reviewed that issue and confirmed it absent.
- `null`: not reviewed, unknown, or not assessable.

`set` is derived automatically. Any true label makes a video `adversarial`. All three labels must be false for `normal`. Everything else is `unclassified`. Rejecting a bad-product candidate does **not** prove that the video is normal in every category.

Multiple issues share one video row. An artifact approved for bad product and nudity has both labels true, not two generation rows. A GIN index supports JSONB containment filtering:

```sql
SELECT * FROM generations
WHERE tags @> '{"labels":{"bad_product":true}}'::jsonb;
```

The UI lets you play a video, see all product reference images, inspect the artifact/item IDs, and explicitly save all three labels. `image_url` is the primary/backward-compatible reference; `reference_image_urls` is the complete deduplicated list used for bad-product comparison. Prior labels are retained in `tags.history`; simultaneous edits produce a conflict instead of silently overwriting another save.

### 2. `testsets` — reproducible groups of videos

| Column | PostgreSQL type | Meaning |
| --- | --- | --- |
| `uuid` | `uuid`, primary key | Testset ID |
| `name` | `text`, not null | Human-readable name |
| `query` | `jsonb`, not null | Validated selection recipe |
| `items` | `jsonb`, not null | Exact selected membership and frozen human labels |
| `created_at` | `timestamptz` | Materialization time |

`query` is not arbitrary executable SQL. It is this restricted tag selection:

```json
{
  "tags": [
    {"key": "normal", "value": null, "count": 20},
    {"key": "adversarial", "value": "bad_product", "count": 20},
    {"key": "adversarial", "value": "nudity", "count": 20}
  ]
}
```

Each frozen item records the generation ID, artifact ID, assigned slice, human labels, product metadata, provenance, and annotation timestamp. For example:

```json
{
  "generation_id": "a-valid-generation-uuid",
  "artifact_id": "a-valid-artifact-uuid",
  "tag_index": 1,
  "set": "adversarial",
  "labels": {"bad_product": true, "bad_movement": null, "nudity": null},
  "metadata": {},
  "provenance": {},
  "annotation_updated_at": "2026-10-08T03:00:00+00:00"
}
```

The selector is deterministic for the same library state: it sorts candidates using their artifact IDs and tag position. It fills the exact requested quotas, with one assignment per artifact. It uses quota matching rather than greedy sampling so overlapping tags do not consume another tag's only valid candidates. If the requested unique combination cannot be filled, preview/create fails with availability details. It does not silently make a smaller dataset or invent normal labels.

After saving, membership is immutable through the API. A new run uses the saved `items`, not a fresh query. Make another testset to change membership. JSON-array IDs are validated by the application at creation time; PostgreSQL cannot attach ordinary row foreign keys to each JSON array entry. There is no generation/testset delete API.

### 3. `modes` — model identities only

| Column | PostgreSQL type | Meaning |
| --- | --- | --- |
| `uuid` | `uuid`, primary key | Mode ID |
| `name` | `text`, unique | For example `cosmos4fps` or `qwenA3b4fps` |
| `created_at`, `updated_at` | `timestamptz` | Identity timestamps |

Modes contain no prompt or configuration. Worker-specific runtime details are supplied by the worker. Rename a mode only for future runs; completed run snapshots keep the original mode name.

### 4. `recipes` — named prompts and label policy

| Column | PostgreSQL type | Meaning |
| --- | --- | --- |
| `uuid` | `uuid`, primary key | Recipe ID |
| `name` | `text`, unique | Prompt name/version |
| `prompt` | `text` | Instructions sent to the model worker |
| `label_definitions` | `jsonb` | Meaning of bad product, bad movement, and nudity for this recipe |
| `created_at`, `updated_at` | `timestamptz` | Recipe timestamps |

Create and edit recipes in the **Recipes** tab. When a benchmark is queued, the selected recipe is copied into its snapshot. Later recipe edits do not alter old runs.

The deployed lifestyle recipes are:

- **AutoQC lifestyle v2 · reviewed exposure** — matches the broad reviewed lifestyle queue, including the explicit exposure categories used by the dataset (`midriff_exposure`, `visible_thighs`, `garment_shifted`, `coverage_reduced_vs_reference`, and `body_contour_visible`).
- **AutoQC lifestyle v2 · strict private exposure** — marks only intimate/private-area exposure and does not treat thighs, midriff, cleavage, or body contour alone as nudity.

Choose the first recipe for the existing broad Tika exposure labels and the second when the benchmark must mean strict private exposure. A run always keeps the exact recipe snapshot it started with.

### 5. `benchmarks` — one mode + recipe + testset

### 4. `benchmarks` — one mode on one testset

| Column | PostgreSQL type | Meaning |
| --- | --- | --- |
| `uuid` | `uuid`, primary key | Run ID |
| `mode_id` | `uuid`, foreign key → `modes.uuid` | Selected mode |
| `testset_id` | `uuid`, foreign key → `testsets.uuid` | Selected frozen testset |
| `recipe_id` | `uuid`, foreign key → `recipes.uuid` | Selected prompt recipe |
| `status` | `text` | `queued`, `running`, `completed`, `cancelled`, or `failed` |
| `predictions` | `jsonb` | Per-artifact predictions/evidence or explicit inference errors |
| `numbers` | `jsonb` | Per-label confusion counts, metrics, and progress |
| `config_snapshot` | `jsonb` | Mode name, recipe prompt/definitions, testset query, and protocol version at run creation |
| `worker_id` | `text`, nullable | Machine worker identifier, not a human reviewer name |
| `lease_token` | `uuid`, nullable | Private, rotating worker ownership token |
| `lease_expires_at` | `timestamptz`, nullable | Recovery deadline if worker stops |
| `error` | `text`, nullable | Run-level error information |
| `created_at`, `started_at`, `finished_at` | `timestamptz` | Run lifecycle timestamps |

Comparing two models creates **two benchmark rows** referring to the same testset and recipe. A worker claims a queued job atomically; two workers cannot own the same run. Every submitted prediction is saved immediately in a transaction. Retrying an identical prediction is safe; submitting a different result for an already-saved video produces a conflict. Expired jobs can be reclaimed and return only videos without saved results. Cancelled runs keep their stored predictions.

Worker leases are never exposed by ordinary benchmark reads or exports. No GPU code runs inside FastAPI request handling.

## How the UI works

- **Overview:** unique video counts, approved issue coverage, frozen testsets, recent runs, and missing-normal warning.
- **Video library:** set/issue filters, ID/title search, streamed videos, product references, label editing, and adding an artifact by ID.
- **Testsets:** deterministic preview, availability errors, frozen selection, and inspectable membership.
- **Model modes:** registered Cosmos/Qwen identities only.
- **Recipes:** create named prompts and label definitions.
- **Benchmarks:** select a recipe, testset, and one or several modes; compare results with live progress, cancellation, per-video human-vs-model labels, and snapshots.
- **Deletion:** modes and recipes can be deleted only when no benchmark refers to them. Terminal benchmark runs can be deleted after confirmation; queued or running runs must be cancelled first. These actions are irreversible.
- **Export:** download database records as JSON without worker credentials.

Video players use a 3:4 frame with `object-fit: contain`; autoplay is muted for browser compatibility. Range requests stream the video from an artifact-service presigned S3 URL through the API. No MP4 library is downloaded to the Mac or H200. Signed URLs are short-lived and are not stored as permanent video locations.

## How to read the numbers

For each issue label:

- **TP:** human issue present and model issue present.
- **FN:** human issue present and model issue absent.
- **FP:** human issue absent and model issue present.
- **TN:** human issue absent and model issue absent.
- **Caught / detection rate:** `TP / all human-positive videos`. This answers “how many of our human-confirmed issues did this model catch?” Pending videos, abstentions, and inference failures cannot inflate this number.
- **Precision:** `TP / (TP + FP)`.
- **Recall, judged:** `TP / (TP + FN)` on successful, non-abstaining predictions only. Read it with detection rate and coverage, not alone.
- **Coverage:** judged predictions / videos with known human truth for that label.

Unknown human labels are excluded rather than treated as negative. Unknown model answers and inference failures are reported separately. An inference error is not a prediction that the video is good. Metrics with no valid denominator are null and appear as `—`.

An adversarial-only set cannot establish normal-video false-positive performance. Human-reviewed normal videos are needed. Legacy “nudity” positives use an exposure-risk policy including visible thighs/body contour; they should not be reported as pure explicit-nudity ground truth without relabeling.

## Source import

The initial import reads existing approvals from both the legacy review JSON and the bulk queue's SQLite review table. It joins metadata from the SQLite catalog, CSV manifests, and product-image map. SQLite is opened read-only with a stable read transaction; no source file or source database is changed.

```bash
cd /home/thouseef.ahmed/autoqc
.venv/bin/python -m autoqc.import_reviews \
  --source /home/thouseef.ahmed/video-review-ui/lifestyle_video_exports
```

The import is idempotent, deduplicates artifact IDs, merges approved labels, and preserves explicit manual edits made in AutoQC. Rejected candidates are not imported as normal videos. Source decision changes withdraw imported positives to unknown rather than fabricate a confirmed-negative review. Existing frozen testsets remain unchanged.

Initial imported state: **336 unique videos**, with **200 bad product**, **78 bad movement**, and **63 exposure-policy positives**. Five videos have two approved categories. There are **zero confirmed-normal videos** in these sources. A real-data starter testset freezes 20 videos per adversarial category, **60 unique videos total, with no normal slice**. The requested 20 normal + 20 bad product + 20 nudity composition needs reviewed normals before it can be created.

## Connecting workers later

Workers use a pull protocol over HTTP, authenticated with `AUTOQC_WORKER_TOKEN` from the private environment file. Your worker needs no PostgreSQL access.

1. `POST /api/worker/claim`: supply `worker_id` and supported mode names. Returns a job or `{"job":null}`.
2. `POST /api/worker/{run_id}/heartbeat`: renew the lease periodically.
3. `POST /api/worker/{run_id}/result`: submit artifact ID plus labels/evidence or an explicit inference error.
4. `POST /api/worker/{run_id}/complete`: finish only after every frozen video has a result or error.

Use `POST /api/worker/{run_id}/fail` with an error and lease token for a run-level failure. Already saved predictions remain available for inspection.

Every worker request uses `Authorization: Bearer <worker-secret>`. Result/heartbeat/complete requests also contain the job's `lease_token`. Human labels, issue provenance, and testset slice names are deliberately excluded from the worker payload, so inference cannot simply copy the answer. The worker receives the video URL, product context, every available product reference image, selected mode name, and frozen recipe prompt. For old testsets that contain only `image_url`, the worker falls back to that one image.

A reference client is included in `worker_client.py`. Implement your actual inference function in `my_worker.py`:

```python
def infer(item, recipe):
    # Call your actual model here. Do not return fabricated test predictions.
    # item['video_url'] streams the video; item['metadata'] has product context.
    # Honor recipe['prompt'] and recipe['label_definitions'].
    return actual_model_result
```

It must return:

```json
{
  "labels": {"bad_product": true, "bad_movement": false, "nudity": null},
  "evidence": {"frames": ["frame references or timestamps"]}
}
```

The supplied OpenAI-compatible services can be used with the included adapter:

```bash
python worker_client.py --url http://10.12.46.7:8810 \
  --adapter autoqc.model_worker:infer --models qwenA3b4fps --worker-id qwen-worker-1
```

The adapter maps `qwenA3b4fps` to `http://prod.dev.internal/llm/v1` and model
`Qwen3.6-35B-A3B`. It maps `cosmos4fps` to `http://prod.dev.internal/cosmos/v1`
and model `Cosmos3-Super`. The drivetrain priority header defaults to
`experimental` and can be changed with `AUTOQC_LLM_PRIORITY`. Endpoint and
model overrides are available as `AUTOQC_QWENA3B4FPS_URL`/
`AUTOQC_QWENA3B4FPS_MODEL` and `AUTOQC_COSMOS4FPS_URL`/
`AUTOQC_COSMOS4FPS_MODEL`.

Video sampling is worker configuration, not a mode-table column. Defaults are
2 FPS for Qwen and 4 FPS for Cosmos. Override them before starting the worker:

```bash
export AUTOQC_QWENA3B4FPS_FPS=2
export AUTOQC_COSMOS4FPS_FPS=4
```

Alternatively, set exactly one `AUTOQC_<MODE>_NUM_FRAMES` variable to request a
fixed frame count. The adapter sends Qwen's `media_io_kwargs` sampling plus
the matching `mm_processor_kwargs` hand-off, and Cosmos's `media_io_kwargs`
sampling. It records the effective sampling setting in each result's evidence.
LLM inference has no client-side timeout by default, so queued gateway work is
allowed to finish. Set `AUTOQC_LLM_TIMEOUT` explicitly only when a deadline is
required; worker heartbeats continue while inference is running.

At the time of writing, Qwen's `/v1/models` endpoint is healthy. Cosmos's
gateway is returning HTTP 502, so do not claim a Cosmos run until its gateway
is healthy; the database mode remains an identity only and is not changed.

The reference client sends heartbeats while inference runs, retries transient network failures, saves each video result separately, and records adapter exceptions as inference failures. No example adapter fabricates predictions in the production application.

## Operations and durability

H200 has user-level systemd services, enabled with lingering so they survive SSH logout and can start after reboot:

```bash
systemctl --user status autoqc-db autoqc-api autoqc-backup.timer
systemctl --user restart autoqc-api
journalctl --user -u autoqc-api -n 50 --no-pager
```

Database files are in `data/postgres`; consistent backups are in `backups`. A daily timer runs at 02:30 in the H200 host's timezone. A backup was also taken during deployment. Manual backup:

```bash
.venv/bin/python deploy/backup.py
.venv/bin/python deploy/verify_backup.py
```

Backups are on the same machine and are **not** protection against losing the entire host. Copy backups off-host using your team's approved storage for that level of resilience. Restore into a separate empty database first using the bundled PostgreSQL `pg_restore`; do not overwrite a live database without an explicit recovery plan. No script deletes old backups automatically.

Secrets/runtime/database directories are private. PostgreSQL listens only on loopback with password authentication. The shared UI intentionally has no reviewer-name field and no per-user authentication; it is suitable for a trusted internal network only. Browser cross-origin writes are blocked. Add SSO/TLS before exposing it outside that network. Worker credentials are separate from the UI.

The H200-to-Mac VPN exhibited a path-MTU issue where large HTTP bodies stalled. `autoqc.serve` limits TCP MSS on its own listener to 1200 bytes; no global networking or existing services are altered. TLS verification for media remains enabled; use `MEDIA_CA_FILE` if your environment needs an internal CA.

For a new host with Docker privileges, `compose.yaml` provides PostgreSQL plus the API and a persistent named volume. Set private `POSTGRES_PASSWORD` and `AUTOQC_WORKER_TOKEN` variables, then use `docker compose up -d --build`. On this H200 host, PostgreSQL was installed in the application's runtime directory from Ubuntu packages without sudo or modifications to system package state.

## Verification

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

Without `TEST_DATABASE_URL`, core tests run and PostgreSQL integration tests skip. With a test database URL, integration tests create an isolated, randomly named schema, exercise real PostgreSQL and the API, then remove only that test-owned schema. They do not change production tables or human labels.

Tests cover unknown-vs-normal semantics, the five-table schema, deterministic selection, overlap quotas, shortages, confusion counts, immutable snapshots, recipe selection, concurrent-save conflicts, worker authentication, expired leases, cancellation, idempotent results/imports, and browser-origin protection.

Read-only browser checks:

```bash
.venv/bin/pip install playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/browser_smoke.py http://10.12.46.7:8810
```

Browser screenshots are generated under `data/screenshots` and are not committed. The existing video-review UI on port 8786 remains separate and unchanged.

The deployed dependency versions are frozen in `requirements.lock`. To reproduce that environment, install this lock file before installing the project with `--no-deps`. Deployment verification passed **19 backend tests**, read-only desktop/mobile browser checks including muted autoplay and S3 playback, idempotent re-import, and a real PostgreSQL backup restore into a separate disposable database.
