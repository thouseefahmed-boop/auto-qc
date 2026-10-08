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
    config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benchmarks (
    uuid uuid PRIMARY KEY,
    mode_id uuid NOT NULL REFERENCES modes(uuid),
    testset_id uuid NOT NULL REFERENCES testsets(uuid),
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
CREATE INDEX IF NOT EXISTS benchmarks_queue ON benchmarks(status, created_at);
CREATE INDEX IF NOT EXISTS benchmarks_testset ON benchmarks(testset_id, created_at DESC);

