CREATE TABLE IF NOT EXISTS edge_issuers (
  cik TEXT PRIMARY KEY, ticker TEXT NOT NULL, company TEXT NOT NULL,
  confidence REAL NOT NULL, source TEXT NOT NULL, active INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edge_issuers_active ON edge_issuers(active, ticker);
CREATE TABLE IF NOT EXISTS edge_events (
  event_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_url TEXT NOT NULL,
  ticker TEXT NOT NULL, cik TEXT, form TEXT, accession TEXT, headline TEXT,
  accepted_at TEXT, published_at TEXT, first_seen_at TEXT NOT NULL,
  polarity TEXT NOT NULL, material INTEGER NOT NULL, stage0_sent_at TEXT,
  enrichment_queued_at TEXT
);
