-- Additive only. Queue indexes contain unresolved work rather than alert history.
ALTER TABLE edge_candidates ADD COLUMN source_item_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_edge_source_item ON edge_candidates(source,source_item_id) WHERE source_item_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_edge_delivery_due ON edge_events(material,analysis_status,first_seen_at,event_id,enrichment_retry_at) WHERE material=1 AND analysis_status='complete' AND (enrichment_ack_at IS NULL OR (actionable=1 AND stage0_sent_at IS NULL));
CREATE INDEX IF NOT EXISTS idx_edge_candidate_due ON edge_candidates(first_seen_at,verification_retry_at) WHERE completed_at IS NULL AND suppression_reason IN ('issuer_unresolved','source_unavailable','classification_uncertain');
CREATE INDEX IF NOT EXISTS idx_edge_event_time ON edge_events(first_seen_at);
