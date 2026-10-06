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
CREATE TABLE IF NOT EXISTS paper_audit (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id TEXT NOT NULL UNIQUE REFERENCES paper_signals(signal_id),
  idempotency_key TEXT NOT NULL UNIQUE,
  recorded_at TEXT NOT NULL, source_hash TEXT NOT NULL, input_hash TEXT NOT NULL,
  previous_hash TEXT NOT NULL, record_hash TEXT NOT NULL, metadata_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS paper_audit_no_update BEFORE UPDATE ON paper_audit
BEGIN SELECT RAISE(ABORT, 'paper audit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS paper_audit_no_delete BEFORE DELETE ON paper_audit
BEGIN SELECT RAISE(ABORT, 'paper audit is immutable'); END;
CREATE TABLE IF NOT EXISTS forward_candidates (
  candidate_key TEXT PRIMARY KEY, event_id TEXT NOT NULL,
  first_observed_at TEXT NOT NULL, snapshot_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS forward_candidates_no_update BEFORE UPDATE ON forward_candidates
BEGIN SELECT RAISE(ABORT, 'forward candidate is immutable'); END;
CREATE TRIGGER IF NOT EXISTS forward_candidates_no_delete BEFORE DELETE ON forward_candidates
BEGIN SELECT RAISE(ABORT, 'forward candidate is immutable'); END;

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
CREATE TABLE IF NOT EXISTS catalyst_evidence (
  evidence_id TEXT PRIMARY KEY, ticker TEXT NOT NULL,
  source_url TEXT NOT NULL, published_at TEXT, retrieved_at TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watch_universe (
  ticker TEXT PRIMARY KEY,
  company TEXT,
  cik TEXT,
  source TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

-- Historical Intelligence Pipeline.  The case, its point-in-time feature snapshot,
-- and its outcome label are deliberately separate so labels cannot leak into inputs.
CREATE TABLE IF NOT EXISTS historical_cases (
  case_id TEXT PRIMARY KEY,
  legacy_event_id TEXT UNIQUE REFERENCES events(id),
  ticker TEXT NOT NULL,
  catalyst_type TEXT NOT NULL,
  event_at TEXT NOT NULL,
  announcement_session TEXT NOT NULL DEFAULT 'unknown',
  provenance_json TEXT NOT NULL DEFAULT '[]',
  legacy_post_hoc INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feature_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES historical_cases(case_id),
  as_of TEXT NOT NULL,
  payload TEXT NOT NULL,
  provenance_json TEXT NOT NULL DEFAULT '[]',
  blinded INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(case_id, as_of)
);
CREATE TABLE IF NOT EXISTS outcome_labels (
  label_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES historical_cases(case_id),
  labeled_at TEXT NOT NULL,
  payload TEXT NOT NULL,
  provenance_json TEXT NOT NULL DEFAULT '[]',
  verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(case_id, labeled_at)
);
CREATE TABLE IF NOT EXISTS change_events (
  change_id TEXT PRIMARY KEY, detected_at TEXT NOT NULL, ticker TEXT,
  event_id TEXT, candidate_id TEXT, nct_id TEXT, asset TEXT,
  change_type TEXT NOT NULL, previous_value TEXT, new_value TEXT,
  severity TEXT NOT NULL, source_url TEXT, source_type TEXT,
  verification_state TEXT NOT NULL, source_hash TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_change_events_detected ON change_events(detected_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_change_events_no_update BEFORE UPDATE ON change_events
BEGIN SELECT RAISE(ABORT, 'change events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_change_events_no_delete BEFORE DELETE ON change_events
BEGIN SELECT RAISE(ABORT, 'change events are append-only'); END;
CREATE TABLE IF NOT EXISTS monitor_observations (
  observation_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, content_hash TEXT NOT NULL,
  source_url TEXT, source_type TEXT, observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS monitor_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
  finished_at TEXT, status TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS security_lifecycle (
  ticker TEXT PRIMARY KEY, company TEXT, status TEXT NOT NULL CHECK(status IN ('ACTIVE','ACQUIRED','DELISTED','RENAMED','BANKRUPT','SUSPENDED','UNKNOWN')),
  effective_from TEXT, effective_to TEXT, successor_ticker TEXT, acquirer_ticker TEXT, reason TEXT,
  source_url TEXT, source_type TEXT, published_at TEXT, verified_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS security_listing_audit (
  ticker TEXT PRIMARY KEY REFERENCES security_lifecycle(ticker),
  exchange TEXT, financial_status TEXT, source_url TEXT NOT NULL, checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_ownership (
  asset_id TEXT NOT NULL, owner_ticker TEXT NOT NULL, effective_from TEXT NOT NULL, effective_to TEXT,
  relationship TEXT NOT NULL, source_url TEXT NOT NULL, source_type TEXT NOT NULL, published_at TEXT, verified_at TEXT NOT NULL,
  PRIMARY KEY(asset_id, owner_ticker, effective_from)
);
-- Immutable evidence captured during a historical backfill.  The payload is
-- deliberately stored separately from the case/snapshot/label records.
CREATE TABLE IF NOT EXISTS source_archive (
  source_id TEXT PRIMARY KEY,
  canonical_url TEXT NOT NULL,
  source_type TEXT NOT NULL,
  published_at TEXT,
  retrieved_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS price_ingestion_runs (
  run_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS historical_price_attachments (
  case_id TEXT NOT NULL REFERENCES historical_cases(case_id),
  ticker TEXT NOT NULL,
  benchmark TEXT NOT NULL DEFAULT 'XBI',
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  provider TEXT NOT NULL,
  run_id TEXT REFERENCES price_ingestion_runs(run_id),
  attached_at TEXT NOT NULL,
  PRIMARY KEY(case_id, ticker, benchmark)
);
-- Price values frozen for the attached case. Shared ticker prices are only an
-- ingestion staging area and must never make another case backtest-eligible.
CREATE TABLE IF NOT EXISTS historical_case_prices (
  case_id TEXT NOT NULL REFERENCES historical_cases(case_id),
  ticker TEXT NOT NULL,
  date TEXT NOT NULL,
  close REAL NOT NULL,
  volume REAL,
  source TEXT NOT NULL,
  PRIMARY KEY(case_id, ticker, date)
);
CREATE TRIGGER IF NOT EXISTS trg_source_archive_no_update BEFORE UPDATE ON source_archive
BEGIN SELECT RAISE(ABORT, 'source archive is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_source_archive_no_delete BEFORE DELETE ON source_archive
BEGIN SELECT RAISE(ABORT, 'source archive is immutable'); END;

-- Breaking-catalyst delivery: transactional outbox independent of Pages publish.
CREATE TABLE IF NOT EXISTS alert_outbox (
  alert_id TEXT PRIMARY KEY,
  change_id TEXT NOT NULL,
  stage TEXT NOT NULL CHECK(stage IN ('stage1','stage2')),
  channel TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','sending','sent','failed','dead')),
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  send_after TEXT NOT NULL,
  sent_at TEXT,
  last_error TEXT,
  UNIQUE(change_id, stage, channel)
);
CREATE INDEX IF NOT EXISTS idx_alert_outbox_pending
  ON alert_outbox(status, send_after, created_at);
-- Cross-source corroboration: later reports of an already-alerted story are linked, not re-pushed.
CREATE TABLE IF NOT EXISTS alert_links (
  change_id TEXT PRIMARY KEY,
  primary_change_id TEXT NOT NULL,
  ticker TEXT,
  similarity REAL NOT NULL,
  source_type TEXT,
  linked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_links_primary ON alert_links(primary_change_id);
CREATE TABLE IF NOT EXISTS alert_deliveries (
  delivery_id TEXT PRIMARY KEY,
  alert_id TEXT NOT NULL REFERENCES alert_outbox(alert_id),
  provider_message_id TEXT,
  sent_at TEXT NOT NULL,
  ack_at TEXT,
  error_code TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS latency_events (
  latency_id TEXT PRIMARY KEY,
  change_id TEXT,
  alert_id TEXT,
  source_type TEXT,
  source_published_at TEXT,
  source_first_seen_at TEXT NOT NULL,
  change_created_at TEXT,
  alert_queued_at TEXT,
  alert_sent_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_latency_events_seen ON latency_events(source_first_seen_at DESC);
CREATE TABLE IF NOT EXISTS source_registry (
  source_id TEXT PRIMARY KEY,
  ticker TEXT,
  cik TEXT,
  source_type TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  feed_url TEXT,
  confidence REAL NOT NULL DEFAULT 0.5,
  active INTEGER NOT NULL DEFAULT 1,
  discovered_at TEXT NOT NULL,
  last_success_at TEXT,
  last_item_at TEXT,
  last_error TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  etag TEXT,
  last_modified TEXT,
  content_hash TEXT,
  provenance TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_source_registry_active ON source_registry(active, source_type);
