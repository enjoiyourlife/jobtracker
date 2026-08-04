-- ─────────────────────────────────────────────────────────────
-- jobtracker schema
-- SQLite. Applied by db/connection.py on startup.
-- ─────────────────────────────────────────────────────────────

PRAGMA foreign_keys = ON;

-- Poller execution log.
-- A job's absence only means "closed" if the run that missed it
-- actually succeeded. This table is how we know that.
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ats         TEXT    NOT NULL,
    company     TEXT    NOT NULL,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    status      TEXT    NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'success', 'failed')),
    jobs_seen   INTEGER NOT NULL DEFAULT 0,
    jobs_new    INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);

-- Companies we poll. slug is the ATS board token, not a display name.
CREATE TABLE IF NOT EXISTS companies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    ats        TEXT    NOT NULL,
    slug       TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    UNIQUE (ats, slug)
);

-- One row per posting.
-- global_id = "{ats}:{slug}:{ats_job_id}" — stable across runs,
-- unique across every ATS. This is what upserts match on.
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    global_id   TEXT    NOT NULL UNIQUE,
    company_id  INTEGER NOT NULL REFERENCES companies(id),
    ats_job_id  TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    location    TEXT,
    absolute_url TEXT   NOT NULL,
    description TEXT,
    raw_payload TEXT    NOT NULL,   -- full JSON, verbatim, for backfill
    first_seen  TEXT    NOT NULL,
    last_seen   TEXT    NOT NULL,   -- fact about our poller
    closed_at   TEXT                -- fact about the job; NULL = open
);

CREATE INDEX IF NOT EXISTS idx_jobs_company  ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_open     ON jobs(closed_at) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_lastseen ON jobs(last_seen);