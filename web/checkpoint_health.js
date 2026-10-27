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
  const parse = value => { try { return MozesCheckpointTime(value, now); } catch { return NaN; } };
  const sources = data?.health?.source_freshness || [];
  const scoped = sources.filter(row => row.in_scope !== false);
  const recent = scoped.filter(row => { const t=parse(row.last_checked_at); return Number.isFinite(t) && now-t<=2700000; });
  const stale = scoped.filter(row => !recent.includes(row));
  const successful = scoped.filter(row => !['no_official_site','no_rss_found'].includes(row.observation_status))
    .map(row => ({at:row.last_success_at, time:parse(row.last_success_at)}))
    .filter(row => Number.isFinite(row.time)).sort((a,b) => b.time-a.time);
  const latest = successful[0];
  const published = parse(data?.generated_at);
  const covered = new Set((data?.official_feeds || []).map(row => row.ticker));
  const backlog = data?.edge_health?.backlog || {};
  const coveredRows = recent.filter(row => {
    if (!row.covered_by || !['FAILED','PARTIAL'].includes(row.status)) return false;
    // Older snapshots have no separate alternate timestamp; retain their label
    // only inside the same recent source window. New snapshots use 30 minutes.
    if (!row.alternate_checked_at) return true;
    const t = parse(row.alternate_checked_at);
    return Number.isFinite(t) && now-t <= 1800000;
  });
  return {checkpoint_at: valid ? at : null, age_minutes: age, stale: !valid || now - parsed > 2700000,
    publication_delayed:valid && now-parsed>=1800000,
    checkpoint_to_export_minutes:valid && Number.isFinite(published) && published>=parsed ? Math.floor((published-parsed)/60000) : null,
    source_scan_at:latest?.at || null, sources_stale:!latest || now-latest.time>2700000,
    publication_at:Number.isFinite(published) ? data.generated_at : null,
    publication_stale:!Number.isFinite(published) || now-published>2700000,
    failed_sources:scoped.filter(row => row.status==='FAILED' || row.status==='PARTIAL').length,
    current_failed_sources:recent.filter(row => row.status==='FAILED').length,
    current_partial_sources:recent.filter(row => row.status==='PARTIAL').length,
    covered_sources:coveredRows.length,
    uncovered_sources:recent.filter(row => ['FAILED','PARTIAL'].includes(row.status) && !coveredRows.includes(row)).length,
    stale_sources:stale.length,
    unconfigured_observations:sources.length-scoped.length,
    ir_coverage:data?.ir_coverage || null,
    unscanned_ir_issuers:(data?.watch_universe || []).filter(row => !covered.has(row.ticker)).length,
    enrichment_pending:Number.isSafeInteger(backlog.enrichment_pending) ? backlog.enrichment_pending : null,
    analysis_pending:Number.isSafeInteger(backlog.analysis_pending) ? backlog.analysis_pending : null,
    enrichment_eligible:Number.isSafeInteger(backlog.enrichment_eligible) ? backlog.enrichment_eligible : null,
    analysis_blocked:Number.isSafeInteger(backlog.analysis_blocked) ? backlog.analysis_blocked : null};
};

globalThis.MozesSourceHealthDetails = function(rows, now = Date.now()) {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time = value => {
    try {
      const at = MozesCheckpointTime(value, now);
      const label = new Intl.DateTimeFormat('he-IL', {timeZone:'Asia/Jerusalem',year:'numeric',month:'2-digit',day:'2-digit',
        hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(new Date(at));
      return `<time datetime="${esc(value)}" title="${esc(value)}">${esc(label)}</time>`;
    }
    catch { return 'לא ידוע'; }
  };
  const labels = {active:'פיד פעיל',html_fallback:'אינדקס HTML בלבד',no_rss_found:'לא נמצא פיד או אינדקס הודעות מאומת',
    no_official_site:'לא הוגדר אתר רשמי',fetch_error:'בקשת המקור נכשלה'};
  const alternateLabels = {official_mirror:'אתר רשמי חלופי',wire_issuer:'פיד GlobeNewswire של החברה',sec_8k_6k:'דיווחי SEC בלבד'};
  const link = value => {
    try {
      const u = new URL(value);
      if (u.protocol !== 'https:' || u.username || u.password) return '';
      return `<a href="${esc(u.href)}" target="_blank" rel="noreferrer">פתח מקור ↗</a>`;
    } catch { return ''; }
  };
  const table = entries => `<div class="table-wrap"><table class="source-health"><caption>מצב מקורות בתמונת הנתונים; גיבוי חלופי מספק כיסוי חלקי בלבד</caption><thead><tr><th scope="col">מקור ומצב</th><th scope="col">סיבת המצב</th><th scope="col">בדיקה והצלחה אחרונה</th><th scope="col">מקור חלופי</th></tr></thead><tbody>${entries.map(row => {
    let checked;
    try { checked = MozesCheckpointTime(row.last_checked_at, now); } catch { checked = NaN; }
    const stale = !Number.isFinite(checked) || now-checked > 2700000;
    const alternates = (row.alternates || []).map(alt => {
      let at;
      try { at = MozesCheckpointTime(alt.checked_at, now); } catch { at = NaN; }
      const fresh = Number.isFinite(at) && now-at <= 1800000;
      const state = alt.status === 'active' ? (fresh ? 'פעיל · כיסוי חלקי' : 'בדיקה ישנה או לא ידועה') : 'נכשל';
      return `<div>${esc(alternateLabels[alt.kind] || 'מקור חלופי')} · ${state}<br>נבדק: ${time(alt.checked_at)}${alt.error?'<br>'+esc(alt.error):''}<br>${link(alt.feed)}</div>`;
    }).join('') || (row.covered_by ? esc(alternateLabels[row.covered_by] || 'מקור חלופי')+' · כיסוי חלקי בלבד' : row.status==='OK' ? 'המקור הראשי תקין' : 'אין גיבוי מאומת בתמונה');
    const reason = /\b403\b/.test(row.last_error || '') ? 'הגישה נדחתה (HTTP 403)' : /\b429\b/.test(row.last_error || '') ? 'המקור הגביל את קצב הבקשות (HTTP 429)' : labels[row.observation_status] || row.observation_status || 'לא נמסרה סיבה';
    return `<tr><th scope="row"><bdi>${esc(row.source)}</bdi><br>${esc(row.status)}${stale?' · בדיקה ישנה או לא ידועה':''}</th><td>${esc(reason)}${row.last_error?'<br><bdi>'+esc(row.last_error)+'</bdi>':''}<br>${link(row.source_url)}</td><td>בדיקה: ${time(row.last_checked_at)}<br>הצלחה: ${time(row.last_success_at)}<br>כשלים רצופים: ${esc(row.consecutive_failures ?? 'לא ידוע')}</td><td>${alternates}</td></tr>`;
  }).join('')}</tbody></table></div>`;
  const rank = {FAILED:0,PARTIAL:1,OK:2};
  const active = rows.filter(row => row.in_scope !== false).sort((a,b) => (rank[a.status]??1)-(rank[b.status]??1));
  const outside = rows.filter(row => row.in_scope === false);
  return (active.length ? table(active) : '<p>אין בדיקות מקורות מתועדות בתמונה.</p>') +
    (outside.length ? `<details><summary>תצפיות מחוץ להיקף המוגדר (${outside.length}) — אינן נכללות בספירת הכשלים</summary>${table(outside)}</details>` : '');
};
