-- Historian database schema, version acme-historian/1 (docs/HISTORIAN_DATA_MODEL.md).
--
-- One file per benchmark session; it may hold several operational scopes. Every row belongs to one
-- scope. All times are simulation seconds (t) or the simulated calendar; no wall-clock time is stored.

CREATE TABLE IF NOT EXISTS historian_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per operational scope (one simulation, from its creation or reset to the next).
CREATE TABLE IF NOT EXISTS scopes (
    operational_scope_id TEXT PRIMARY KEY,                 -- OS-<32 hex>, never a run id
    simulation_start     TEXT NOT NULL,                    -- ISO-8601 on the simulated calendar
    duration_seconds     INTEGER NOT NULL,
    sample_period_s      INTEGER NOT NULL,                 -- step-state sampling grid of this recording
    schema               TEXT NOT NULL
);

-- Committed, continuously observed intervals [from_t, to_t] (both inclusive). Anything outside is
-- "not recorded", which is different from "unchanged".
CREATE TABLE IF NOT EXISTS coverage (
    operational_scope_id TEXT NOT NULL REFERENCES scopes (operational_scope_id),
    from_t               INTEGER NOT NULL,
    to_t                 INTEGER NOT NULL,
    CHECK (from_t <= to_t)
);
CREATE INDEX IF NOT EXISTS coverage_by_scope ON coverage (operational_scope_id, from_t);

-- Entity nodes and records, with their ISA-95 placement from the operational projection.
CREATE TABLE IF NOT EXISTS entities (
    operational_scope_id TEXT NOT NULL REFERENCES scopes (operational_scope_id),
    entity_id            TEXT NOT NULL,                    -- canonical id <entity_type>:<native_id>
    entity_type          TEXT NOT NULL,
    parent_id            TEXT,
    isa95_path           TEXT NOT NULL,                    -- JSON array of canonical ids, enterprise first
    isa95_mapping_id     TEXT,
    name                 TEXT,
    first_t              INTEGER NOT NULL,
    PRIMARY KEY (operational_scope_id, entity_id)
);
CREATE INDEX IF NOT EXISTS entities_by_parent ON entities (operational_scope_id, parent_id);

-- One series per (scope, entity, variable).
CREATE TABLE IF NOT EXISTS series (
    series_id            INTEGER PRIMARY KEY,
    operational_scope_id TEXT NOT NULL,
    entity_id            TEXT NOT NULL,
    variable             TEXT NOT NULL,                    -- e.g. measurement:XMEAS(9), setpoint, vibration
    kind                 TEXT NOT NULL CHECK (kind IN ('xmeas', 'xmv', 'setpoint', 'controller_output',
                                                          'production', 'property')),
    unit                 TEXT,
    semantics            TEXT NOT NULL CHECK (semantics IN ('step_state', 'analyzer_sample')),
    sample_period_s      INTEGER NOT NULL,                 -- grid (step_state) or catalog period (analyzer)
    dead_time_s          INTEGER,                          -- analyzer_sample only (catalog)
    source               TEXT NOT NULL,
    UNIQUE (operational_scope_id, entity_id, variable),
    FOREIGN KEY (operational_scope_id, entity_id) REFERENCES entities (operational_scope_id, entity_id)
);

CREATE TABLE IF NOT EXISTS samples (
    series_id INTEGER NOT NULL REFERENCES series (series_id),
    t         INTEGER NOT NULL,
    value     REAL,                                        -- NULL: not a number at that time
    quality   TEXT,                                        -- XMEAS only: GOOD | BAD
    PRIMARY KEY (series_id, t)
) WITHOUT ROWID;

-- Property-level changes of operational state (entities, records, the site lifecycle).
-- seq orders rows within a scope, shared with events: (t, seq) is the history order.
CREATE TABLE IF NOT EXISTS state_changes (
    operational_scope_id TEXT NOT NULL,
    entity_id            TEXT NOT NULL,
    property             TEXT NOT NULL,
    t                    INTEGER NOT NULL,
    seq                  INTEGER NOT NULL,
    value                TEXT NOT NULL,                    -- canonical JSON
    unit                 TEXT,
    origin               TEXT NOT NULL CHECK (origin IN ('baseline', 'change')),
    PRIMARY KEY (operational_scope_id, seq),
    FOREIGN KEY (operational_scope_id, entity_id) REFERENCES entities (operational_scope_id, entity_id)
);
CREATE INDEX IF NOT EXISTS state_by_entity ON state_changes (operational_scope_id, entity_id, property, t, seq);
CREATE INDEX IF NOT EXISTS state_by_time ON state_changes (operational_scope_id, t, seq);

-- Operational events (OE-/LC- ids) only.
CREATE TABLE IF NOT EXISTS events (
    operational_scope_id TEXT NOT NULL,
    event_id             TEXT NOT NULL,
    t                    INTEGER NOT NULL,
    seq                  INTEGER NOT NULL,
    event_type           TEXT NOT NULL,
    entity_id            TEXT NOT NULL,
    source               TEXT,
    severity             TEXT,
    payload              TEXT NOT NULL,                    -- canonical JSON
    causation_id         TEXT,
    correlation_id       TEXT,
    PRIMARY KEY (operational_scope_id, event_id),
    UNIQUE (operational_scope_id, seq),
    FOREIGN KEY (operational_scope_id, entity_id) REFERENCES entities (operational_scope_id, entity_id)
);
CREATE INDEX IF NOT EXISTS events_by_time ON events (operational_scope_id, t, seq);
CREATE INDEX IF NOT EXISTS events_by_entity ON events (operational_scope_id, entity_id, t, seq);
