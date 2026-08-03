-- Session Manager schema (v2 guide: "writes a new session record into the
-- PostgreSQL Database" + "register their lineage history"). Separate,
-- dedicated Postgres instance from Langfuse's — different concern, different
-- lifecycle, no reason to couple the two.

CREATE TABLE IF NOT EXISTS sessions (
    session_id      UUID PRIMARY KEY,
    kio_id          TEXT NOT NULL,
    task_type       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | running | completed | failed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lineage (
    id              BIGSERIAL PRIMARY KEY,
    session_id      UUID NOT NULL REFERENCES sessions(session_id),
    kio_id          TEXT NOT NULL,
    envelope_id     UUID NOT NULL,
    status          TEXT NOT NULL,                     -- ok | error
    output          JSONB,
    error           TEXT,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lineage_session_id ON lineage(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_kio_id ON sessions(kio_id);
