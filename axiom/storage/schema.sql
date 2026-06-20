-- AXIOM SQLite data model (spec §7 retention contract).
--
-- Design rules encoded here:
--  * Every record carries a stable decision_id; the chain decisions -> orders ->
--    fills, plus outcomes / postmortems, joins by key (never free-text parse).
--  * Feature tags the learning loop buckets on are first-class columns, NOT
--    re-derived from the thesis prose.
--  * inputs_ref makes any decision (incl. PASS) reconstructable.
--  * Raw decision/outcome/forecast records are kept indefinitely (calibration
--    fuel). Time-decay is a weighting in computation, never deletion.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- The §8 pre-trade decision record. One row per decision, incl. PASS / SHADOW.
CREATE TABLE IF NOT EXISTS decisions (
    decision_id     TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    mode            TEXT NOT NULL,         -- LIVE | PAPER | SHADOW
    symbol          TEXT NOT NULL,
    decision        TEXT NOT NULL,         -- ENTER | PASS | ADJUST | EXIT
    regime          TEXT NOT NULL,
    -- structured feature columns (first-class, not parsed from thesis)
    iv_rank         REAL,
    iv_percentile   REAL,
    sector_cluster  TEXT,
    catalyst_type   TEXT,
    dte_bucket      TEXT,
    days_to_earnings INTEGER,
    conviction      INTEGER,
    -- economics & sizing snapshots
    structure_type  TEXT,
    dte             INTEGER,
    credit_or_debit REAL,
    max_profit      REAL,
    max_loss        REAL,
    pop             REAL,
    ev_after_slippage REAL,
    ev_per_dollar_risk REAL,
    kelly_fraction  REAL,
    applied_fraction REAL,
    contracts       INTEGER,
    capital_at_risk REAL,
    pct_of_nlv      REAL,
    thesis          TEXT,
    invalidation    TEXT,
    inputs_ref      TEXT NOT NULL,         -- ref/hash of the input snapshot
    payload_json    TEXT NOT NULL          -- full §8 JSON for fidelity
);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(timestamp);
CREATE INDEX IF NOT EXISTS idx_decisions_features
    ON decisions(structure_type, regime, dte_bucket);

-- Every evaluated candidate, taken or not (spec §7).
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id    TEXT PRIMARY KEY,
    decision_id     TEXT,                  -- set if this candidate became a decision
    timestamp       TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    structure_type  TEXT,
    iv_rank         REAL,
    pop             REAL,
    ev_after_slippage REAL,
    ev_per_dollar_risk REAL,
    rejected_reason TEXT,                   -- NULL if accepted
    payload_json    TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decisions(decision_id)
);
CREATE INDEX IF NOT EXISTS idx_candidates_decision ON candidates(decision_id);

CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    decision_id     TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    mode            TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT,                   -- net OPEN | CLOSE
    status          TEXT NOT NULL,          -- SIMULATED | SUBMITTED | FILLED | CANCELLED | REJECTED
    limit_price     REAL,
    payload_json    TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decisions(decision_id)
);
CREATE INDEX IF NOT EXISTS idx_orders_decision ON orders(decision_id);

CREATE TABLE IF NOT EXISTS fills (
    fill_id         TEXT PRIMARY KEY,
    order_id        TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    fill_price      REAL NOT NULL,          -- net per-contract price actually filled
    contracts       INTEGER NOT NULL,
    fees            REAL DEFAULT 0,
    simulated       INTEGER NOT NULL,       -- 1 = paper, 0 = live
    payload_json    TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);
CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);

CREATE TABLE IF NOT EXISTS positions (
    position_id     TEXT PRIMARY KEY,
    decision_id     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    structure_type  TEXT,
    contracts       INTEGER NOT NULL,
    opened_at       TEXT NOT NULL,
    closed_at       TEXT,
    status          TEXT NOT NULL,          -- OPEN | CLOSED
    max_loss_total  REAL,
    net_delta       REAL DEFAULT 0,
    net_gamma       REAL DEFAULT 0,
    net_vega        REAL DEFAULT 0,
    net_theta       REAL DEFAULT 0,
    payload_json    TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decisions(decision_id)
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);

-- Realized result per closed decision (spec §11 Layer 1).
CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id      TEXT PRIMARY KEY,
    decision_id     TEXT NOT NULL,
    resolved_at     TEXT NOT NULL,
    realized_pnl    REAL NOT NULL,
    win             INTEGER NOT NULL,       -- 1/0
    invalidation_triggered INTEGER DEFAULT 0,
    realized_vs_modeled REAL,               -- realized_pnl - modeled EV
    payload_json    TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decisions(decision_id)
);
CREATE INDEX IF NOT EXISTS idx_outcomes_decision ON outcomes(decision_id);

-- Standalone predictions with a resolution date (spec §11 Layer 1).
CREATE TABLE IF NOT EXISTS forecasts (
    forecast_id     TEXT PRIMARY KEY,
    decision_id     TEXT,                   -- optional link
    timestamp       TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    statement       TEXT NOT NULL,          -- "NVDA holds $X through Friday"
    predicted_prob  REAL NOT NULL,          -- the AI's stated probability
    resolution_date TEXT NOT NULL,
    resolved        INTEGER DEFAULT 0,
    resolved_outcome INTEGER,               -- 1/0 once resolved
    payload_json    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_forecasts_resolution ON forecasts(resolution_date, resolved);

-- Structured post-mortems (spec §11 Layer 5: process vs outcome).
CREATE TABLE IF NOT EXISTS postmortems (
    postmortem_id   TEXT PRIMARY KEY,
    decision_id     TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    quadrant        TEXT NOT NULL,          -- good_process_won | good_process_lost | flawed_process_won | flawed_process_lost
    lesson          TEXT NOT NULL,
    error_flags     TEXT,                   -- JSON list of recurring error tags
    payload_json    TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decisions(decision_id)
);
CREATE INDEX IF NOT EXISTS idx_postmortems_decision ON postmortems(decision_id);

-- Persisted learned state (spec §11 Layers 2-3): survives restarts.
CREATE TABLE IF NOT EXISTS learning_state (
    key             TEXT PRIMARY KEY,       -- e.g. 'calibration_map', 'bucket:PCS|CHOP|ivr50'
    updated_at      TEXT NOT NULL,
    value_json      TEXT NOT NULL
);

-- Rolling aggregates and tier history (spec §7).
CREATE TABLE IF NOT EXISTS metrics (
    metric_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    name            TEXT NOT NULL,          -- brier | log_loss | expectancy | tier | nlv
    value           REAL,
    context         TEXT,                   -- optional bucket / label
    payload_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name, timestamp);
