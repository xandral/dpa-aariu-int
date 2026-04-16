-- =====================================================================
-- Web Page Integrity Monitor — Database schema (PostgreSQL)
-- =====================================================================
--
-- This file documents the production schema. At runtime the application
-- creates the same structure via SQLAlchemy `Base.metadata.create_all`,
-- so this file is descriptive, not the source of truth.
--
-- Two tables, one head pointer:
--
--   urls       → configuration + monitoring metadata
--   snapshots  → unified table for both reference baselines and check
--                results, discriminated by `kind`
--
--   urls.current_baseline_id → snapshots.id     (head pointer pattern)
--   snapshots.url_id         → urls.id          (ownership)
--
-- The two FKs form a cycle. We break it at DDL time by creating the
-- snapshots-side FK inline and adding the urls-side FK with ALTER TABLE
-- afterwards (this is what SQLAlchemy `use_alter=True` emits).
-- =====================================================================


-- ---------------------------------------------------------------------
-- ENUM types
-- ---------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE urlstatus AS ENUM ('active', 'inactive');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE snapshotkind AS ENUM ('baseline', 'check');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE checkstatus AS ENUM ('OK', 'CHANGED', 'ALERT', 'ERROR');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ---------------------------------------------------------------------
-- Table: urls
-- One row per monitored URL. Holds configuration, per-URL thresholds
-- and a pointer to the currently-active baseline snapshot.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS urls (
    id                       UUID         PRIMARY KEY,
    url                      VARCHAR      NOT NULL UNIQUE,

    -- Monitoring configuration
    frequency                INTEGER      NOT NULL DEFAULT 3600,        -- seconds
    status                   urlstatus    NOT NULL DEFAULT 'active',

    -- AI models used for this URL's analysis pipeline
    embedding_model          VARCHAR      NOT NULL DEFAULT 'text-embedding-3-small',
    llm_model                VARCHAR      NOT NULL DEFAULT 'gpt-4o-mini',

    -- Per-URL thresholds for the analysis funnel
    diff_threshold_ok        FLOAT        NOT NULL DEFAULT 5.0,         -- % below this → OK
    diff_threshold_alert     FLOAT        NOT NULL DEFAULT 50.0,        -- % above this → ALERT
    cosine_threshold_ok      FLOAT        NOT NULL DEFAULT 0.95,        -- cos above this → OK
    cosine_threshold_alert   FLOAT        NOT NULL DEFAULT 0.5,         -- cos below this → ALERT

    -- Pointer to the currently-active baseline snapshot.
    -- Refresh is non-destructive: a new row is inserted in `snapshots`
    -- and this pointer is moved.  Old baselines remain in the table.
    -- FK constraint added below via ALTER TABLE to break the cycle.
    current_baseline_id      UUID         NULL,

    last_checked_at          TIMESTAMPTZ  NULL,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Speeds up the scheduler poll: "active URLs whose check interval expired"
CREATE INDEX IF NOT EXISTS ix_urls_status_last_checked ON urls (status, last_checked_at);


-- ---------------------------------------------------------------------
-- Table: snapshots
-- Unified store for both reference baselines (kind='baseline') and
-- periodic check records (kind='check').
--
--   kind='baseline': html_raw, text_clean, embedding populated;
--                    diff_percentage / similarity_score / status / llm_analysis NULL.
--   kind='check':    all fields populated (similarity / llm may be NULL
--                    when the analysis funnel resolves at level 1).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS snapshots (
    id                UUID          PRIMARY KEY,
    url_id            UUID          NOT NULL REFERENCES urls(id) ON DELETE CASCADE,
    kind              snapshotkind  NOT NULL,

    -- Page content (always populated)
    html_raw          TEXT          NOT NULL,
    text_clean        TEXT          NOT NULL,
    embedding         JSON          NULL,                                -- list[float], OpenAI text-embedding-3-small

    -- Analysis results (populated only when kind='check')
    diff_percentage   FLOAT         NULL,                                -- 0..100
    similarity_score  FLOAT         NULL,                                -- 0..1, NULL if funnel stopped at level 1
    status            checkstatus   NULL,
    llm_analysis      JSON          NULL,                                -- only when funnel reached level 3
    error_message     TEXT          NULL,                                -- fetch/analysis error, mutually exclusive with llm_analysis

    created_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- Composite index covering both query patterns:
--   "latest check for URL X"        → WHERE url_id=? AND kind='check' ORDER BY created_at DESC LIMIT 1
--   "baseline history for URL X"    → WHERE url_id=? AND kind='baseline' ORDER BY created_at DESC
CREATE INDEX IF NOT EXISTS ix_snapshots_url_kind_created ON snapshots (url_id, kind, created_at);


-- ---------------------------------------------------------------------
-- Cyclic FK: urls.current_baseline_id → snapshots.id
-- Added after both tables exist (the SQLAlchemy `use_alter=True` pattern).
-- The pointer is nullable so a Url row can be inserted before the first
-- baseline snapshot exists; the application sets it within the same
-- transaction right after inserting the baseline.
-- ---------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_urls_current_baseline'
    ) THEN
        ALTER TABLE urls
            ADD CONSTRAINT fk_urls_current_baseline
            FOREIGN KEY (current_baseline_id)
            REFERENCES snapshots(id);
    END IF;
END;
$$;
