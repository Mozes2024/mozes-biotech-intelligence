// Export-time freshness is refreshed using the reader's clock on every render.
globalThis.MozesCheckpointHealth = function(data, now = Date.now()) {
  const at = data?.checkpoint_at;
  const parsed = typeof at === 'string' && /(?:Z|\+00:00)$/.test(at) ? Date.parse(at) : NaN;
  const valid = Number.isFinite(parsed) && parsed <= now;
  const age = valid ? Math.floor((now - parsed) / 60000) : null;
  return {checkpoint_at: valid ? at : null, age_minutes: age, stale: !valid || now - parsed > 2700000};
};
