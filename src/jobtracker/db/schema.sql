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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    global_id    TEXT    NOT NULL UNIQUE,
    company_id   INTEGER NOT NULL REFERENCES companies(id),
    ats_job_id   TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    location     TEXT,
    absolute_url TEXT    NOT NULL,
    description  TEXT,
    raw_payload  TEXT    NOT NULL,  -- full JSON, verbatim, for backfill
    updated_at   TEXT,              -- posting's own last-modified, per the ATS
    first_seen   TEXT    NOT NULL,  -- write-once
    last_seen    TEXT    NOT NULL,  -- fact about our poller
    closed_at    TEXT               -- fact about the job; NULL = open
);

CREATE INDEX IF NOT EXISTS idx_jobs_company   ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_open      ON jobs(closed_at) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_lastseen  ON jobs(last_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_updated   ON jobs(updated_at);

-- Roles being pursued. A row exists only once a decision has been made,
-- so this table means "acted on" rather than "seen". Discovery lives in
-- jobs; queue membership is the absence of a row here.
--
-- job_id is UNIQUE: one application per posting, enforced by the
-- database rather than by remembering to check.
CREATE TABLE IF NOT EXISTS applications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    status         TEXT    NOT NULL DEFAULT 'queued'
                   CHECK (status IN (
                       'queued', 'skipped', 'submitted', 'acknowledged',
                       'screening', 'interview', 'offer', 'rejected'
                   )),
    score          INTEGER,         -- score when queued, for later analysis
    notes          TEXT,
    queued_at      TEXT    NOT NULL,
    submitted_at   TEXT,            -- set once, on transition to 'submitted'
    last_status_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_apps_submitted ON applications(submitted_at)
    WHERE submitted_at IS NOT NULL;

-- The queue positions shown by the last `jobtracker queue` run.
-- Overwritten wholesale on every run — only the latest listing is kept.
-- `apply <position>` resolves against this table rather than
-- recomputing rankings, so it acts on exactly what was displayed
-- instead of a live query that may have shifted since.
CREATE TABLE IF NOT EXISTS queue_snapshot (
    position   INTEGER NOT NULL PRIMARY KEY,
    job_id     INTEGER NOT NULL REFERENCES jobs(id),
    created_at TEXT    NOT NULL
);