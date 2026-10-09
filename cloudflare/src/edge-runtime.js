// Durable Object telemetry is local evidence, never authoritative account usage.
export const POLICY_VERSION = 'clinical-cache-v1';
export const seconds = (env, key, fallback, min = 60, max = 86400) =>
  Math.max(min, Math.min(max, Number(env[key]) || fallback));
export const digest = async value => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))))
  .map(x => x.toString(16).padStart(2, '0')).join('');

export function budgetMode(previous, fraction) {
  if (fraction >= .60) return 'PROTECTION';
  if (previous === 'PROTECTION' && fraction >= .50) return previous;
  if (fraction >= .40) return 'ECO';
  if (previous === 'ECO' && fraction >= .30) return previous;
  return 'NORMAL';
}

export class Usage {
  constructor(snapshot, now = Date.now()) {
    this.data = snapshot || {};
    this.roll(now);
  }
  roll(now = Date.now()) {
    const day = new Date(now).toISOString().slice(0, 10);
    if (this.data.day !== day) this.data = {day, started_at: now, operations: {}, mode: 'NORMAL'};
  }
  record(operation, result, error = false) {
    this.roll();
    const row = this.data.operations[operation] ||= {queries: 0, errors: 0, rows_read: 0, rows_written: 0, measured: 0, unmeasured: 0};
    row.queries++;
    if (error) row.errors++;
    const meta = result?.meta;
    if (Number.isFinite(meta?.rows_read) && Number.isFinite(meta?.rows_written)) {
      row.measured++; row.rows_read += meta.rows_read; row.rows_written += meta.rows_written;
    } else row.unmeasured++;
  }
  report(env, now = Date.now()) {
    this.roll(now);
    const rows = Object.values(this.data.operations);
    const elapsed = Math.max(3600, (now - this.data.started_at) / 1000);
    // Unmeasured requests reserve a conservative configurable scan/write allowance.
    const reads = rows.reduce((n, r) => n + r.rows_read + r.unmeasured * seconds(env, 'EDGE_UNMEASURED_READ_ROWS', 5000, 1, 1000000), 0);
    const writes = rows.reduce((n, r) => n + r.rows_written + r.unmeasured * 10, 0);
    const projected_read = reads * 86400 / elapsed + Math.max(0, Number(env.EDGE_OTHER_DAILY_READ_ROWS) || 1000000);
    const projected_write = writes * 86400 / elapsed + Math.max(0, Number(env.EDGE_OTHER_DAILY_WRITE_ROWS) || 10000);
    const fraction = Math.max(projected_read / 5000000, projected_write / 100000);
    this.data.mode = budgetMode(this.data.mode, fraction);
    return {...this.data, projected_read, projected_write, estimate_basis: 'worker-local D1 metadata plus configured external reserve; not account totals',
      account_usage_available: false, partial_day: this.data.started_at > Date.parse(this.data.day),
      warning: this.data.mode === 'NORMAL' ? null : 'Estimated quota pressure; source intervals extended, coverage may be delayed',
      primary_consumers: Object.entries(this.data.operations).sort((a,b) => b[1].rows_read - a[1].rows_read).slice(0,5).map(([operation,counts])=>({operation,...counts}))};
  }
}

export function instrumentDatabase(db, usage, prefix = '') {
  const wrap = (statement, operation) => ({
    bind(...args) { return wrap(statement.bind(...args), operation); },
    async all() { try { const r = await statement.all(); if(r.success===false)throw Error('D1 operation failed'); usage.record(operation,r); return r; } catch(e) { usage.record(operation,null,true); throw e; } },
    async first() { const r = await this.all(); return r.results?.[0] || null; },
    async run() { try { const r = await statement.run(); if(r.success===false)throw Error('D1 operation failed'); usage.record(operation,r); return r; } catch(e) { usage.record(operation,null,true); throw e; } },
    original: statement, operation,
  });
  return {
    prepare(sql) {
      const table = sql.match(/(?:FROM|INTO|UPDATE)\s+(\w+)/i)?.[1] || 'other';
      const operation=sql.includes('candidate_id=?')?'candidate_id':sql.includes("analysis_status IN")?'analysis_pending':sql.includes('LIMIT 20')?'candidate_queue':sql.includes('LIMIT 2001')?'event_metrics':sql.includes('LIMIT 3')?'delivery_queue':sql.includes('COUNT(*)')?'aggregate':sql.includes('stage0_sent_at=?')?'stage0_receipt':sql.includes('enrichment_attempts=enrichment_attempts')?'dispatch_claim':'other';
      return wrap(db.prepare(sql), prefix + sql.trim().split(/\s/)[0].toLowerCase() + ':' + table + ':' + operation);
    },
    async batch(statements) {
      try { const results = await db.batch(statements.map(s=>s.original)); if(results.some(r=>r.success===false))throw Error('D1 batch failed'); results.forEach((r,i)=>usage.record(statements[i].operation,r)); return results; }
      catch(e) { statements.forEach(s=>usage.record(s.operation,null,true)); throw e; }
    },
  };
}

// The two statements execute in one D1 transaction. JSON avoids variable/statement
// limits for a 5,000-issuer snapshot; predicates avoid equivalent row updates.
export async function applyIssuerSnapshot(db, rows, now = new Date().toISOString()) {
  const payload = JSON.stringify(rows);
  return db.batch([
    db.prepare("UPDATE edge_issuers SET active=0,updated_at=? WHERE active=1 AND cik NOT IN (SELECT json_extract(value,'$.cik') FROM json_each(?))").bind(now,payload),
    db.prepare(`INSERT INTO edge_issuers(cik,ticker,company,confidence,source,active,updated_at)
      SELECT json_extract(value,'$.cik'),json_extract(value,'$.ticker'),json_extract(value,'$.company'),json_extract(value,'$.confidence'),json_extract(value,'$.source'),1,? FROM json_each(?) WHERE 1
      ON CONFLICT(cik) DO UPDATE SET ticker=excluded.ticker,company=excluded.company,confidence=excluded.confidence,source=excluded.source,active=1,updated_at=excluded.updated_at
      WHERE edge_issuers.ticker<>excluded.ticker OR edge_issuers.company<>excluded.company OR edge_issuers.confidence<>excluded.confidence OR edge_issuers.source<>excluded.source OR edge_issuers.active<>1`).bind(now,payload),
  ]);
}

export async function persistSecBatch(db, events) {
  if(!events.length)return;
  await db.prepare(`INSERT OR IGNORE INTO edge_events(event_id,source,source_url,ticker,cik,form,accession,headline,accepted_at,published_at,first_seen_at,polarity,material,filing_index_url,analysis_status)
    SELECT json_extract(value,'$.event_id'),'sec',json_extract(value,'$.source_url'),json_extract(value,'$.ticker'),json_extract(value,'$.cik'),json_extract(value,'$.form'),json_extract(value,'$.accession'),json_extract(value,'$.headline'),json_extract(value,'$.accepted_at'),NULL,json_extract(value,'$.first_seen_at'),'unknown',0,json_extract(value,'$.source_url'),'pending' FROM json_each(?)`).bind(JSON.stringify(events)).run();
}

export async function persistWireBatch(db, items, extraStatements = []) {
  if(!items.length) {if(extraStatements.length)await db.batch(extraStatements);return;}
  const payload=JSON.stringify(items);
  const sameEvidence="edge_candidates.content_hash=excluded.content_hash AND edge_candidates.classification_json=excluded.classification_json AND edge_candidates.ticker IS excluded.ticker AND edge_candidates.cik IS excluded.cik AND edge_candidates.source_url=excluded.source_url";
  if(new TextEncoder().encode(payload).length>1800000)throw Error('wire persistence bound exceeded');
  await db.batch([
    db.prepare(`INSERT INTO edge_candidates(candidate_id,source,source_url,headline,summary,published_at,first_seen_at,ticker,cik,ticker_hint,classification_json,lifecycle,suppression_reason,history_json,content_hash,source_item_id)
      SELECT json_extract(value,'$.event_id'),json_extract(value,'$.source'),json_extract(value,'$.source_url'),json_extract(value,'$.headline'),json_extract(value,'$.summary'),json_extract(value,'$.published_at'),json_extract(value,'$.first_seen_at'),json_extract(value,'$.ticker'),json_extract(value,'$.cik'),json_extract(value,'$.ticker_hint'),json_extract(value,'$.classification_json'),json_extract(value,'$.lifecycle'),json_extract(value,'$.suppression_reason'),json_extract(value,'$.history_json'),json_extract(value,'$.content_hash'),json_extract(value,'$.source_item_id') FROM json_each(?) WHERE 1
      ON CONFLICT(candidate_id) DO UPDATE SET headline=excluded.headline,summary=excluded.summary,source_url=excluded.source_url,ticker=excluded.ticker,cik=excluded.cik,ticker_hint=excluded.ticker_hint,classification_json=excluded.classification_json,lifecycle=CASE WHEN ${sameEvidence} THEN edge_candidates.lifecycle ELSE excluded.lifecycle END,
      suppression_reason=CASE WHEN ${sameEvidence} THEN edge_candidates.suppression_reason WHEN edge_candidates.completed_at IS NOT NULL AND (edge_candidates.content_hash<>excluded.content_hash OR edge_candidates.classification_json<>excluded.classification_json OR edge_candidates.ticker IS NOT excluded.ticker OR edge_candidates.cik IS NOT excluded.cik) THEN 'classification_uncertain' ELSE excluded.suppression_reason END,
      history_json=CASE WHEN ${sameEvidence} THEN edge_candidates.history_json ELSE (SELECT json_group_array(json(value)) FROM (SELECT value FROM (SELECT value,CAST(key AS INTEGER) AS ordinal FROM json_each(edge_candidates.history_json) UNION ALL SELECT value,100+CAST(key AS INTEGER) AS ordinal FROM json_each(excluded.history_json) ORDER BY ordinal DESC LIMIT 10) ORDER BY ordinal)) END,
      content_hash=excluded.content_hash,source_item_id=excluded.source_item_id,completed_at=CASE WHEN ${sameEvidence} THEN edge_candidates.completed_at ELSE NULL END,github_run_id=CASE WHEN ${sameEvidence} THEN edge_candidates.github_run_id ELSE NULL END,verification_retry_at=CASE WHEN ${sameEvidence} THEN edge_candidates.verification_retry_at ELSE NULL END
      WHERE edge_candidates.content_hash<>excluded.content_hash OR edge_candidates.classification_json<>excluded.classification_json OR edge_candidates.ticker IS NOT excluded.ticker OR edge_candidates.cik IS NOT excluded.cik OR edge_candidates.source_url<>excluded.source_url OR edge_candidates.source_item_id IS NOT excluded.source_item_id`).bind(payload),
    db.prepare(`INSERT OR IGNORE INTO edge_events(event_id,source,source_url,ticker,cik,form,headline,published_at,first_seen_at,polarity,material,analysis_status,analysis_completed_at,relevant,actionable,catalyst,classification_json,lifecycle,suppression_reason)
      SELECT json_extract(value,'$.event_id'),json_extract(value,'$.source'),json_extract(value,'$.source_url'),json_extract(value,'$.ticker'),json_extract(value,'$.cik'),'rss',json_extract(value,'$.headline'),json_extract(value,'$.published_at'),json_extract(value,'$.first_seen_at'),json_extract(value,'$.polarity'),1,'complete',json_extract(value,'$.first_seen_at'),json_extract(value,'$.relevant'),json_extract(value,'$.actionable'),json_extract(value,'$.catalyst'),json_extract(value,'$.classification_json'),'CLASSIFIED',json_extract(value,'$.suppression_reason') FROM json_each(?) WHERE json_extract(value,'$.ticker') IS NOT NULL AND json_extract(value,'$.material')=1`).bind(payload),
    db.prepare(`WITH incoming AS (SELECT json_extract(value,'$.event_id') AS event_id,value FROM json_each(?))
      UPDATE edge_events SET source_url=json_extract(incoming.value,'$.source_url'),headline=json_extract(incoming.value,'$.headline'),ticker=COALESCE(json_extract(incoming.value,'$.ticker'),edge_events.ticker),cik=COALESCE(json_extract(incoming.value,'$.cik'),edge_events.cik),
      material=CASE WHEN json_extract(incoming.value,'$.ticker') IS NULL THEN 0 ELSE json_extract(incoming.value,'$.material') END,actionable=CASE WHEN json_extract(incoming.value,'$.ticker') IS NULL THEN 0 ELSE json_extract(incoming.value,'$.actionable') END,relevant=json_extract(incoming.value,'$.relevant'),catalyst=json_extract(incoming.value,'$.catalyst'),polarity=json_extract(incoming.value,'$.polarity'),classification_json=json_extract(incoming.value,'$.classification_json'),lifecycle='CLASSIFIED',suppression_reason=json_extract(incoming.value,'$.suppression_reason')
      FROM incoming WHERE edge_events.event_id=incoming.event_id AND edge_events.enrichment_ack_at IS NULL AND (edge_events.classification_json IS NOT json_extract(incoming.value,'$.classification_json') OR edge_events.source_url<>json_extract(incoming.value,'$.source_url') OR edge_events.headline IS NOT json_extract(incoming.value,'$.headline') OR (json_extract(incoming.value,'$.ticker') IS NULL AND (edge_events.material<>0 OR edge_events.actionable<>0)) OR (json_extract(incoming.value,'$.ticker') IS NOT NULL AND edge_events.ticker<>json_extract(incoming.value,'$.ticker')))` ).bind(payload),
    ...extraStatements,
  ]);
}
