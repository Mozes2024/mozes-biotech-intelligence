const pairs = {
  detection: ["source_time", "first_seen_at"], stage0_delivery: ["first_seen_at", "stage0_sent_at"],
  enrichment_dispatch: ["first_seen_at", "enrichment_dispatch_at"], enrichment_ack: ["first_seen_at", "enrichment_ack_at"],
};
export function statistics(values) {
  values = values.filter(Number.isFinite).sort((a, b) => a - b);
  const n = values.length;
  const quantile = p => {
    if (n < 2) return null;
    const position = (n - 1) * p, lower = Math.floor(position), upper = Math.ceil(position);
    return values[lower] + (values[upper] - values[lower]) * (position - lower);
  };
  return { n, p50: quantile(.5), p95: quantile(.95), p99: quantile(.99), mean: n ? values.reduce((a, b) => a + b, 0) / n : null };
}
function summarize(rows) {
  return Object.fromEntries(Object.entries(pairs).map(([metric, [start, end]]) => [metric, statistics(rows.map(row => {
    const a = Date.parse(start === "source_time" ? row.source === "sec" ? row.accepted_at : row.published_at : row[start]);
    const b = Date.parse(row[end]);
    return Number.isFinite(a) && Number.isFinite(b) && b >= a ? (b - a) / 1000 : NaN;
  }))]));
}
export function calculateMetrics(rows, now = new Date().toISOString(), truncated = false) {
  const windows = {};
  for (const hours of [1, 24]) {
    const cutoff = Date.parse(now) - hours * 3600000;
    const cohort = rows.filter(row => Date.parse(row.first_seen_at) >= cutoff && Date.parse(row.first_seen_at) <= Date.parse(now));
    windows[`${hours}h`] = { rows: cohort.length, metrics: summarize(cohort),
      by_source: Object.fromEntries([...new Set(cohort.map(row => row.source))].map(source =>
        [source, summarize(cohort.filter(row => row.source === source))])) };
  }
  return { generated_at: now, units: "seconds", minimum_percentile_samples: 2, cohort: "first_seen_at", truncated, windows };
}
export async function edgeMetrics(db, now = new Date().toISOString()) {
  const cutoff = new Date(Date.parse(now) - 86400000).toISOString();
  const rows = (await db.prepare("SELECT source,accepted_at,published_at,first_seen_at,stage0_sent_at,enrichment_dispatch_at,enrichment_ack_at FROM edge_events WHERE first_seen_at>=? ORDER BY first_seen_at DESC LIMIT 2001").bind(cutoff).all()).results || [];
  return calculateMetrics(rows.slice(0, 2000), now, rows.length > 2000);
}
