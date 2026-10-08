-- Expand only. Legacy queued_at remains dispatch evidence, never completion.
ALTER TABLE edge_events ADD COLUMN filing_index_url TEXT;
ALTER TABLE edge_events ADD COLUMN analysis_status TEXT NOT NULL DEFAULT 'complete';
ALTER TABLE edge_events ADD COLUMN analysis_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE edge_events ADD COLUMN last_analysis_error TEXT;
ALTER TABLE edge_events ADD COLUMN analysis_completed_at TEXT;
ALTER TABLE edge_events ADD COLUMN analysis_retry_at TEXT;
ALTER TABLE edge_events ADD COLUMN analysis_source_hash TEXT;
ALTER TABLE edge_events ADD COLUMN enrichment_dispatch_at TEXT;
ALTER TABLE edge_events ADD COLUMN enrichment_ack_at TEXT;
ALTER TABLE edge_events ADD COLUMN enrichment_completed_at TEXT;
ALTER TABLE edge_events ADD COLUMN enrichment_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE edge_events ADD COLUMN last_enrichment_error TEXT;
ALTER TABLE edge_events ADD COLUMN enrichment_retry_at TEXT;
ALTER TABLE edge_events ADD COLUMN enrichment_change_id TEXT;
ALTER TABLE edge_events ADD COLUMN enrichment_github_run_id TEXT;
ALTER TABLE edge_events ADD COLUMN enrichment_alert_ids_json TEXT;
ALTER TABLE edge_events ADD COLUMN enrichment_delivery_json TEXT;
UPDATE edge_events SET filing_index_url=source_url, analysis_status='pending' WHERE source='sec';
UPDATE edge_events SET analysis_completed_at=first_seen_at WHERE source<>'sec';
UPDATE edge_events SET enrichment_dispatch_at=enrichment_queued_at,
  enrichment_attempts=CASE WHEN enrichment_queued_at IS NULL THEN 0 ELSE 1 END,
  last_enrichment_error=CASE WHEN enrichment_queued_at IS NULL THEN NULL ELSE 'legacy dispatch has no completion ACK' END;
CREATE INDEX idx_edge_analysis_pending ON edge_events(analysis_status,analysis_retry_at,first_seen_at);
CREATE INDEX idx_edge_enrichment_pending ON edge_events(enrichment_ack_at,enrichment_retry_at,first_seen_at);
