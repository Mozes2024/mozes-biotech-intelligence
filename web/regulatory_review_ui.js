/* B2: inspect retained claims; no approval, ACK, promotion or network requests. */
(function () {
  'use strict';
  const baseSystem = system;
  const unknown = value => esc(value == null || value === '' ? 'לא ידוע' : value);
  function sourceLink(value) {
    try {
      const url = new URL(value);
      if (url.protocol === 'https:' && url.host === 'www.sec.gov' && !url.username && !url.password &&
          /^\/Archives\/edgar\/data\/\d+\//.test(url.pathname)) {
        return `<a href="${esc(value)}" target="_blank" rel="noopener noreferrer">פתיחת המקור הראשוני</a>`;
      }
    } catch (_) { /* Invalid references remain visible as escaped text. */ }
    return '';
  }
  system = function () {
    const queue = D.regulatory_review;
    let html = baseSystem() + '<section class="panel"><div class="panel-head"><h3>בדיקה רגולטורית — קריאה בלבד</h3></div><div class="panel-body">';
    if (!queue?.available) return html + '<p>תור הבדיקה לא זמין בתמונת המצב.</p></div></section>';
    html += `<p>רשומות לבדיקה: ${esc(queue.count)}</p><p class="notice">כל רשומה היא טענה לא מוכרעת ואינה תוצאה רגולטורית מאומתת. המקור והראיות זמינים לבדיקה; אין כאן אישור או דחייה.</p>`;
    if (!queue.items?.length) html += '<p>אין פריטים לבדיקה בתמונת המצב.</p>';
    for (const row of queue.items || []) {
      html += `<details class="source"><summary>${unknown(row.ticker)} · ${unknown(row.review_id)} · ${unknown(row.reason)}</summary>`;
      html += `<p>זהות מנפיק: ${row.issuer_verified ? 'אומתה מול CIK המקור' : 'לא אומתה'} · ${unknown(row.issuer_name)}</p>`;
      const app = row.application;
      html += app ? `<p>בקשה מזוהה: ${unknown(app.id)} · ${unknown(app.program)}</p><p>מצב הבקשה הקיים, בנפרד מהטענה: ${unknown(app.status)} / ${unknown(app.verification_state)}</p>` : '<p>זהות הבקשה לא אומתה; מזהה בראיות הוא טענה בלבד.</p>';
      html += `<p>מצב בדיקה: ${unknown(row.verification_state)} · סיבה: ${unknown(row.reason)}</p>`;
      html += `<p>פורסם במקור: ${unknown(row.published_at)} · נשלף במקור: ${unknown(row.retrieved_at)} · נצפה בתור: ${unknown(row.observed_at)}</p>`;
      html += `<p>מועד אירוע שנטען בראיות, לא מאומת: ${unknown(row.event_timestamp)}</p>`;
      html += `<p class="ltr">מקור מדויק: ${unknown(row.source_url)}</p>${sourceLink(row.source_url)}`;
      if (row.evidence_available === false) html += '<p class="notice">מבנה הראיות לא נתמך; המקור הגולמי נשמר להלן.</p>';
      html += `<h4>ראיות מקוריות — ללא הכרעה</h4><pre class="ltr">${esc(typeof row.evidence === 'string' ? row.evidence : JSON.stringify(row.evidence, null, 2))}</pre></details>`;
    }
    return html + '</div></section>';
  };
})();
