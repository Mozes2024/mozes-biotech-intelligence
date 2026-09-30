PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, url TEXT NOT NULL, source_type TEXT, reliability TEXT,
  published TEXT, retrieved TEXT, title TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('live','historical')), payload TEXT NOT NULL
);
-- Outcomes are stored separately from event payloads (labels only).
CREATE TABLE IF NOT EXISTS event_outcomes (
  event_id TEXT PRIMARY KEY REFERENCES events(id), payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extracted_statements (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, retrieved_at TEXT NOT NULL, statement TEXT NOT NULL,
  catalyst_type TEXT, date_precision TEXT, window_start TEXT, window_end TEXT, confidence INTEGER
);
-- ClinicalTrials.gov v2 does not expose historical versions: store every fetch as a new version.
CREATE TABLE IF NOT EXISTS trial_record_versions (
  nct_id TEXT NOT NULL, retrieved_at TEXT NOT NULL, last_update_posted TEXT, payload TEXT NOT NULL,
  PRIMARY KEY (nct_id, retrieved_at)
);
CREATE TABLE IF NOT EXISTS prices (
  ticker TEXT NOT NULL, date TEXT NOT NULL, close REAL NOT NULL, volume REAL, source TEXT,
  PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS score_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, as_of TEXT NOT NULL,
  created_at TEXT NOT NULL, versions TEXT NOT NULL, result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_signals (
  signal_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, created_at TEXT NOT NULL, as_of TEXT NOT NULL,
  window_start TEXT, window_end TEXT, price REAL, classification TEXT NOT NULL,
  versions TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_outcomes (
  signal_id TEXT PRIMARY KEY REFERENCES paper_signals(signal_id), attached_at TEXT NOT NULL,
  clinical TEXT NOT NULL, move REAL, note TEXT
);

CREATE TRIGGER IF NOT EXISTS trg_score_runs_no_update BEFORE UPDATE ON score_runs
BEGIN SELECT RAISE(ABORT, 'score_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_score_runs_no_delete BEFORE DELETE ON score_runs
BEGIN SELECT RAISE(ABORT, 'score_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_paper_no_update BEFORE UPDATE ON paper_signals
BEGIN SELECT RAISE(ABORT, 'paper signals are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_paper_no_delete BEFORE DELETE ON paper_signals
BEGIN SELECT RAISE(ABORT, 'paper signals are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_paper_outcome_no_update BEFORE UPDATE ON paper_outcomes
BEGIN SELECT RAISE(ABORT, 'paper outcomes are write-once'); END;
CREATE TRIGGER IF NOT EXISTS trg_paper_outcome_no_delete BEFORE DELETE ON paper_outcomes
BEGIN SELECT RAISE(ABORT, 'paper outcomes are write-once'); END;

-- v0.2 live radar metadata. Kept separate from the legacy JSON event payload so
-- existing point-in-time tests remain compatible.
CREATE TABLE IF NOT EXISTS event_state (
  event_id TEXT PRIMARY KEY REFERENCES events(id),
  status TEXT NOT NULL DEFAULT 'CANDIDATE',
  verification_state TEXT NOT NULL DEFAULT 'QUARANTINED',
  verification_confidence INTEGER NOT NULL DEFAULT 0,
  event_timestamp TEXT,
  event_session TEXT NOT NULL DEFAULT 'unknown',
  resolved_at TEXT,
  updated_at TEXT NOT NULL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS event_sources (
  event_id TEXT NOT NULL REFERENCES events(id),
  source_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  url TEXT,
  published_at TEXT,
  statement TEXT,
  supports_date INTEGER NOT NULL DEFAULT 0,
  retrieved_at TEXT NOT NULL,
  PRIMARY KEY (event_id, source_id, retrieved_at)
);
CREATE TABLE IF NOT EXISTS discovery_candidates (
  candidate_id TEXT PRIMARY KEY,
  nct_id TEXT,
  sponsor TEXT,
  ticker TEXT,
  ticker_confidence REAL,
  phase TEXT,
  title TEXT,
  primary_completion TEXT,
  last_update_posted TEXT,
  status TEXT,
  raw_json TEXT NOT NULL,
  discovered_at TEXT NOT NULL,
  promoted_event_id TEXT
);
CREATE TABLE IF NOT EXISTS sponsor_ticker_map (
  sponsor_norm TEXT PRIMARY KEY,
  sponsor TEXT NOT NULL,
  ticker TEXT NOT NULL,
  cik TEXT,
  confidence REAL NOT NULL,
  source TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_validation (
  model_name TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0,
  oos_n INTEGER NOT NULL DEFAULT 0,
  min_oos_n INTEGER NOT NULL,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  note TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refresh_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS watch_universe (
  ticker TEXT PRIMARY KEY,
  company TEXT,
  cik TEXT,
  source TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
