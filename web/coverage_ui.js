/* v2E: timing/provenance are observations, never trading signals. */
const timingDaysV2E = days, timingWindowV2E = win, cardV2E = focusCard;
const renderV2E = render, dashboardV2E = dashboard, changesV2E = changedCompact;
function triggerText(a) {
  const counts = a.trigger_current == null ? 'אין עדכון כמותי מאומת' :
    'אושר לאחרונה: ' + a.trigger_current + (a.trigger_target == null ? '' : '/' + a.trigger_target);
  return 'תלוי אירוע — ' + counts + '; מועד קלנדרי לא ידוע';
}
days = function(a) { return a.timing_mode === 'EVENT_DRIVEN' ? triggerText(a) : timingDaysV2E(a); };
win = function(a) { return a.timing_mode === 'EVENT_DRIVEN' ? esc(triggerText(a)) : timingWindowV2E(a); };
function provenanceCard(a) {
  const p = a.provenance || {};
  const source = (a.sources || [])[0] || {};
  const url = p.source_url || source.url || '';
  const safeUrl = /^https?:\/\//i.test(url) ? url : '';
  const state = (a.state?.status || 'DISCOVERY_ONLY') + ' / ' + (a.state?.verification_state || 'DISCOVERY_ONLY');
  return '<details class="source"><summary>מקור ואימות · ' + esc(state) + '</summary>' +
    '<p>' + esc(p.source_type || source.source_type || 'לא ידוע') + ' · ' +
    (p.primary ? 'מקור ראשוני' : 'יש לבדוק את סוג המקור') + '</p>' +
    '<p>פורסם: ' + esc(p.published_at || source.published_at || 'לא ידוע') +
    ' · נשלף: ' + esc(p.retrieved_at || source.retrieved_at || 'לא ידוע') + '</p>' +
    '<p>' + esc(p.quote || source.statement || '') + '</p>' +
    '<p>שיטת אימות: ' + esc(p.verification_method || 'catalog_provenance') + '</p>' +
    '<p>מזהה: ' + esc(p.accession || p.nct_id || a.nct_id || '—') + ' · דיוק: ' +
    esc(p.trigger_precision || p.date_precision || a.date?.precision || 'unknown') + '</p>' +
    (safeUrl ? '<a target="_blank" rel="noopener noreferrer" href="' + esc(safeUrl) + '">פתיחת המקור</a>' : '') +
    '</details>';
}
focusCard = function(a) { return cardV2E(a).replace('</article>', provenanceCard(a) + '</article>'); };
const sourcesV2E = sources, reasonV2E = reasonLabel;
sources = function(a) { return provenanceCard(a) + sourcesV2E(a); };
reasonLabel = function(reason) {
  return reason === 'event_driven_calendar_unknown' ? 'קטליזטור תלוי אירוע; אין תאריך מאומת' : reasonV2E(reason);
};
changedCompact = function() { return ''; };
dashboard = function() { return changesV2E() + dashboardV2E(); };
render = function() {
  renderV2E();
  document.querySelectorAll('.source details, details.source').forEach(node => {
    node.addEventListener('click', event => event.stopPropagation());
  });
  if (!apiMode) {
    const label = document.getElementById('apiLabel');
    if (label) label.textContent = 'תמונת מצב לקריאה בלבד';
    const refresh = document.getElementById('refreshBtn');
    if (refresh) { refresh.disabled = true; refresh.title = 'רענון זמין ביישום המקומי בלבד'; }
  }
};
const changesDescriptionV2E = changeDescription;
changeDescription = function(c) {
  return ({trigger_progressed:'התקדמות טריגר מאומתת', primary_statement_added:'הצהרה חדשה ממקור ראשוני',
           cash_runway_updated:'עודכן מידע מזומן ומסלול מימון', review_state_changed:'השתנה מצב הבדיקה'}[c.change_type] ||
          changesDescriptionV2E(c));
};
