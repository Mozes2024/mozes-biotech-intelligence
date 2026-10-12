// Export-time freshness is refreshed using the reader's clock on every render.
globalThis.MozesCheckpointTime = function(value, now = Date.now()) {
  const m = typeof value === 'string' && /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!m) throw Error('Invalid checkpoint time');
  const [year, month, day, hour, minute, second] = m.slice(1, 7).map(Number);
  const offsetHour = Number(m[10] || 0), offsetMinute = Number(m[11] || 0);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (month < 1 || month > 12 || day < 1 || day > days[month - 1] || hour > 23 || minute > 59 || second > 59 || offsetHour > 23 || offsetMinute > 59) throw Error('Invalid checkpoint time');
  // Validate source calendar fields before converting its explicit offset to UTC.
  const wall = new Date(0);
  wall.setUTCFullYear(year, month - 1, day);
  wall.setUTCHours(hour, minute, second, Number((m[7] || '').padEnd(3, '0').slice(0, 3)));
  const offset = (offsetHour * 60 + offsetMinute) * 60000 * (m[9] === '-' ? -1 : 1);
  const time = wall.getTime() - offset;
  if (!Number.isFinite(time) || time > now) throw Error('Invalid checkpoint time');
  return time;
};

globalThis.MozesCheckpointHealth = function(data, now = Date.now()) {
  const at = data?.checkpoint_at;
  let parsed;
  try { parsed = MozesCheckpointTime(at, now); } catch { parsed = NaN; }
  const valid = Number.isFinite(parsed);
  const age = valid ? Math.floor((now - parsed) / 60000) : null;
  return {checkpoint_at: valid ? at : null, age_minutes: age, stale: !valid || now - parsed > 2700000};
};
