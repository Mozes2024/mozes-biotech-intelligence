-- Additive: preserve v3/v3.1 rows, dispatch receipts and the DO namespace.
ALTER TABLE edge_events ADD COLUMN relevant INTEGER NOT NULL DEFAULT 0;
ALTER TABLE edge_events ADD COLUMN actionable INTEGER NOT NULL DEFAULT 0;
ALTER TABLE edge_events ADD COLUMN catalyst INTEGER NOT NULL DEFAULT 0;
ALTER TABLE edge_events ADD COLUMN classification_json TEXT;
ALTER TABLE edge_events ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'DISCOVERED';
ALTER TABLE edge_events ADD COLUMN suppression_reason TEXT;
UPDATE edge_events SET relevant=material,actionable=material,
 lifecycle=CASE WHEN enrichment_ack_at IS NOT NULL THEN 'PUBLISHED' WHEN analysis_status='complete' THEN 'CLASSIFIED' ELSE 'DISCOVERED' END;
CREATE TABLE IF NOT EXISTS edge_candidates (
 candidate_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_url TEXT NOT NULL,
 headline TEXT NOT NULL, summary TEXT NOT NULL, published_at TEXT,
 first_seen_at TEXT NOT NULL, ticker TEXT, cik TEXT, ticker_hint TEXT,
 classification_json TEXT NOT NULL, lifecycle TEXT NOT NULL, suppression_reason TEXT,
 history_json TEXT NOT NULL, content_hash TEXT NOT NULL, github_run_id TEXT,
 change_id TEXT, catalyst_id TEXT, completed_at TEXT,
 verification_attempts INTEGER NOT NULL DEFAULT 0, verification_retry_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_edge_candidate_queue ON edge_candidates(lifecycle,first_seen_at);
CREATE INDEX IF NOT EXISTS idx_edge_candidate_issuer ON edge_candidates(ticker,first_seen_at);
