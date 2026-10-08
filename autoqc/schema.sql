CREATE TABLE IF NOT EXISTS generations (
    uuid uuid PRIMARY KEY,
    artifact_id uuid NOT NULL UNIQUE,
    tags jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(tags) = 'object'),
    CHECK (tags->>'set' IN ('normal', 'adversarial', 'unclassified')),
    CHECK (jsonb_typeof(tags->'labels') = 'object')
);
CREATE INDEX IF NOT EXISTS generations_tags_gin ON generations USING gin(tags jsonb_path_ops);
CREATE INDEX IF NOT EXISTS generations_created ON generations(created_at DESC, uuid);

CREATE TABLE IF NOT EXISTS testsets (
    uuid uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 160),
    query jsonb NOT NULL CHECK (jsonb_typeof(query) = 'object'),
    items jsonb NOT NULL CHECK (jsonb_typeof(items) = 'array'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS modes (
    uuid uuid PRIMARY KEY,
    name text NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 120),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE modes DROP COLUMN IF EXISTS config;

-- Runtime endpoint and video-sampling settings live separately from the mode
-- identity. A benchmark snapshots these values when it is queued.
CREATE TABLE IF NOT EXISTS worker_settings (
    mode_id uuid PRIMARY KEY REFERENCES modes(uuid) ON DELETE CASCADE,
    endpoint text NOT NULL CHECK (length(endpoint) BETWEEN 1 AND 500),
    served_model text NOT NULL CHECK (length(served_model) BETWEEN 1 AND 200),
    fps double precision,
    num_frames integer,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (fps IS NULL OR fps > 0),
    CHECK (num_frames IS NULL OR num_frames > 0),
    CHECK ((fps IS NULL) OR (num_frames IS NULL))
);
CREATE INDEX IF NOT EXISTS worker_settings_updated ON worker_settings(updated_at DESC);

CREATE TABLE IF NOT EXISTS recipes (
    uuid uuid PRIMARY KEY,
    name text NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 160),
    prompt text NOT NULL CHECK (length(prompt) BETWEEN 1 AND 20000),
    label_definitions jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(label_definitions) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benchmarks (
    uuid uuid PRIMARY KEY,
    mode_id uuid NOT NULL REFERENCES modes(uuid),
    testset_id uuid NOT NULL REFERENCES testsets(uuid),
    recipe_id uuid REFERENCES recipes(uuid),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','cancelled','failed')),
    predictions jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(predictions) = 'object'),
    numbers jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(numbers) = 'object'),
    config_snapshot jsonb NOT NULL,
    worker_id text,
    lease_token uuid,
    lease_expires_at timestamptz,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz
);
ALTER TABLE benchmarks ADD COLUMN IF NOT EXISTS recipe_id uuid REFERENCES recipes(uuid);
CREATE INDEX IF NOT EXISTS benchmarks_queue ON benchmarks(status, created_at);
CREATE INDEX IF NOT EXISTS benchmarks_testset ON benchmarks(testset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS benchmarks_recipe ON benchmarks(recipe_id, created_at DESC);
