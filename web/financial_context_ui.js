/* v2C: observational cash/financing context. No client-side investment scoring. */
(function () {
  'use strict';
  const labels = {
    registration_only: '\u05e8\u05d9\u05e9\u05d5\u05dd \u05de\u05d3\u05e3 / \u05e8\u05d9\u05e9\u05d5\u05dd \u05e0\u05d9\u05d9\u05e8\u05d5\u05ea \u2014 \u05dc\u05d0 \u05d2\u05d9\u05d5\u05e1 \u05d1\u05e4\u05d5\u05e2\u05dc',
    atm_capacity: '\u05de\u05e1\u05d2\u05e8\u05ea \u05dc\u05de\u05db\u05d9\u05e8\u05ea \u05de\u05e0\u05d9\u05d5\u05ea \u05d1\u05e9\u05d5\u05e7 (ATM)',
    proposed_offering: '\u05d2\u05d9\u05d5\u05e1 \u05de\u05ea\u05d5\u05db\u05e0\u05df',
    priced_offering: '\u05d2\u05d9\u05d5\u05e1 \u05e9\u05e4\u05d5\u05e8\u05e1\u05dd \u05dc\u05d5 \u05de\u05d7\u05d9\u05e8',
    completed_offering: '\u05d3\u05d9\u05d5\u05d5\u05d7 \u05e2\u05dc \u05d4\u05e9\u05dc\u05de\u05ea \u05d2\u05d9\u05d5\u05e1',
    resale_registration: '\u05e8\u05d9\u05e9\u05d5\u05dd \u05de\u05db\u05d9\u05e8\u05d4 \u05e9\u05dc \u05d1\u05e2\u05dc\u05d9 \u05de\u05e0\u05d9\u05d5\u05ea; \u05dc\u05d0 \u05db\u05e1\u05e3 \u05dc\u05d7\u05d1\u05e8\u05d4',
    financing_review_required: '\u05d3\u05d9\u05d5\u05d5\u05d7 \u05de\u05d9\u05de\u05d5\u05df \u05dc\u05d1\u05d3\u05d9\u05e7\u05d4',
    multiple_transactions_review: '\u05db\u05de\u05d4 \u05e2\u05e1\u05e7\u05d0\u05d5\u05ea \u05d1\u05de\u05e1\u05de\u05da \u2014 \u05dc\u05d1\u05d3\u05d9\u05e7\u05d4',
  };
  const baseChangeDescription = changeDescription;
  changeDescription = function (c) {
    if (c.change_type === 'financing_interpreted') return labels[c.new_value?.kind] || '\u05e0\u05d5\u05ea\u05d7 \u05d3\u05d9\u05d5\u05d5\u05d7 \u05de\u05d9\u05de\u05d5\u05df';
    if (c.change_type === 'cash_runway_updated') return '\u05e0\u05ea\u05d5\u05e0\u05d9 \u05de\u05d6\u05d5\u05de\u05e0\u05d9\u05dd \u05d5\u05d0\u05d5\u05de\u05d3\u05df \u05ea\u05d6\u05e8\u05d9\u05dd \u05e2\u05d5\u05d3\u05db\u05e0\u05d5';
    return baseChangeDescription(c);
  };
  const baseFocus = focusCard;
  const baseMarket = market;
  const baseSystem = system;
  const money = x => x == null ? '\u05dc\u05d0 \u05d9\u05d3\u05d5\u05e2' : `$${(x / 1e6).toLocaleString('he-IL', {maximumFractionDigits: 1})}M`;
  const band = f => f.status === 'stale' ? '\u05d4\u05d3\u05d5\u05d7 \u05d9\u05e9\u05df \u05de\u05d3\u05d9' :
    f.status !== 'available' ? '\u05d7\u05e1\u05e8 \u05de\u05d9\u05d3\u05e2 \u05dc\u05d7\u05d9\u05e9\u05d5\u05d1' :
    f.estimated_remaining_months == null ? '\u05dc\u05dc\u05d0 \u05e9\u05e8\u05d9\u05e4\u05d4 \u05ea\u05e4\u05e2\u05d5\u05dc\u05d9\u05ea \u05d1\u05ea\u05e7\u05d5\u05e4\u05d4' :
    `${f.estimated_remaining_months.toFixed(1)} \u05d7\u05d5\u05d3\u05e9\u05d9\u05dd (\u05d0\u05d5\u05de\u05d3\u05df)`;
  function fundingText(code) {
    return ({
      estimate_before_window: '\u05d4\u05d0\u05d5\u05de\u05d3\u05df \u05e7\u05e6\u05e8 \u05de\u05d4\u05d6\u05de\u05df \u05e2\u05d3 \u05d4\u05d0\u05d9\u05e8\u05d5\u05e2: \u05d1\u05d3\u05d5\u05e7 \u05e6\u05d5\u05e8\u05da \u05d1\u05de\u05d9\u05de\u05d5\u05df.',
      estimate_overlaps_window: '\u05d0\u05d5\u05de\u05d3\u05df \u05d4\u05de\u05d6\u05d5\u05de\u05e0\u05d9\u05dd \u05d7\u05d5\u05e4\u05e3 \u05dc\u05d7\u05dc\u05d5\u05df \u05d4\u05d0\u05d9\u05e8\u05d5\u05e2.',
      later_financing_requires_reconciliation: '\u05d3\u05d5\u05d5\u05d7 \u05e2\u05dc \u05d2\u05d9\u05d5\u05e1 \u05de\u05d0\u05d5\u05d7\u05e8 \u05dc\u05ea\u05e7\u05d5\u05e4\u05ea \u05d4\u05d3\u05d5\u05d7. \u05d4\u05d0\u05d5\u05de\u05d3\u05df \u05d8\u05e8\u05dd \u05de\u05d5\u05ea\u05d0\u05dd \u05d0\u05dc\u05d9\u05d5.',
    })[code] || '';
  }
  focusCard = function (a) {
    const f = a.financial_context || {};
    const events = a.financing_events || [];
    const warning = fundingText(a.funding_window?.status);
    let extra = `<div class="simple-signals" style="margin-top:8px">${simpleSignal('\u05d0\u05d5\u05de\u05d3\u05df \u05de\u05e1\u05dc\u05d5\u05dc \u05de\u05d6\u05d5\u05de\u05e0\u05d9\u05dd', band(f))}</div>`;
    if (events.length) extra += `<div class="company">${esc(labels[events[0].kind] || labels.financing_review_required)}</div>`;
    if (warning) extra += `<div class="notice" style="padding:8px;margin-top:8px">${esc(warning)}</div>`;
    return baseFocus(a).replace('<div class="focus-open">', extra + '<div class="focus-open">');
  };
  market = function (a) {
    const f = a.financial_context || {};
    let html = baseMarket(a) + `<section class="section"><h4>\u05de\u05d6\u05d5\u05de\u05e0\u05d9\u05dd \u05d5\u05de\u05d9\u05de\u05d5\u05df</h4><div class="notice">${esc(band(f))}</div>`;
    if (f.period_end) html += `<div class="kv"><span>\u05ea\u05e7\u05d5\u05e4\u05ea \u05d3\u05d5\u05d7</span><span>${esc(f.period_end)} | \u05d4\u05d5\u05d2\u05e9 ${esc(f.filed)}</span></div><div class="kv"><span>\u05de\u05d6\u05d5\u05de\u05df</span><b>${esc(money(f.cash_usd))}</b></div><div class="kv"><span>\u05d4\u05e9\u05e7\u05e2\u05d5\u05ea \u05e0\u05d6\u05d9\u05dc\u05d5\u05ea \u05e9\u05d6\u05d5\u05d4\u05d5</span><b>${esc(money(f.investments_usd))}</b></div><div class="kv"><span>\u05e9\u05e8\u05d9\u05e4\u05d4 \u05ea\u05e4\u05e2\u05d5\u05dc\u05d9\u05ea \u05de\u05de\u05d5\u05e6\u05e2\u05ea \u05dc\u05d7\u05d5\u05d3\u05e9</span><b>${esc(money(f.burn_per_month_usd))}</b></div><p class="company">${esc(f.note_he || '')} ${f.liquidity_basis === 'cash_only' ? '\u05d4\u05d7\u05d9\u05e9\u05d5\u05d1 \u05db\u05d5\u05dc\u05dc \u05de\u05d6\u05d5\u05de\u05df \u05d1\u05dc\u05d1\u05d3; \u05d4\u05d4\u05e9\u05e7\u05e2\u05d5\u05ea \u05dc\u05d0 \u05d0\u05d5\u05de\u05ea\u05d5.' : ''}</p>`;
    if (f.source_url?.startsWith('https://data.sec.gov/')) html += `<a href="${esc(f.source_url)}" target="_blank" rel="noopener noreferrer">\u05de\u05e7\u05d5\u05e8 \u05d4\u05e0\u05ea\u05d5\u05e0\u05d9\u05dd \u05d1\u05beSEC</a>`;
    for (const e of a.financing_events || []) {
      html += `<details class="source"><summary>${esc(e.form)} | ${esc(labels[e.kind] || labels.financing_review_required)} | ${esc(e.published_at?.slice(0,10))}</summary>`;
      for (const fact of e.facts || []) html += `<p class="ltr">${esc(fact.span?.text)}${fact.ambiguous ? ' (\u05db\u05de\u05d4 \u05e0\u05ea\u05d5\u05e0\u05d9\u05dd \u05de\u05d0\u05d5\u05ea\u05d5 \u05e1\u05d5\u05d2)' : ''}</p><blockquote class="ltr">${esc(fact.context)}</blockquote>`;
      if (e.source_url?.startsWith('https://www.sec.gov/')) html += `<a href="${esc(e.source_url)}" target="_blank" rel="noopener noreferrer">\u05e4\u05ea\u05d7 \u05d3\u05d9\u05d5\u05d5\u05d7 \u05de\u05e7\u05d5\u05e8\u05d9</a>`;
      html += '</details>';
    }
    return html + '</section>';
  };
  system = function () {
    const state = D.intelligence_v2c || {};
    return baseSystem() + `<section class="panel"><div class="panel-head"><h3>\u05e9\u05db\u05d1\u05ea \u05de\u05d9\u05de\u05d5\u05df \u05d5\u05d6\u05d9\u05d4\u05d5\u05d9</h3></div><div class="panel-body"><p>\u05d1\u05e7\u05e9\u05d5\u05ea FDA \u05d4\u05de\u05de\u05ea\u05d9\u05e0\u05d5\u05ea \u05dc\u05d0\u05d9\u05de\u05d5\u05ea \u05d6\u05d4\u05d5\u05ea: ${esc(state.regulatory_review_count || 0)}</p><p>\u05d3\u05d9\u05d5\u05d5\u05d7\u05d9 \u05de\u05d9\u05de\u05d5\u05df \u05de\u05e0\u05d5\u05ea\u05d7\u05d9\u05dd: ${esc(state.financing_disclosures || 0)}</p><p class="company">\u05e0\u05ea\u05d5\u05e0\u05d9\u05dd \u05d7\u05e1\u05e8\u05d9\u05dd \u05d0\u05d9\u05e0\u05dd \u05de\u05d5\u05d7\u05dc\u05e4\u05d9\u05dd \u05d1\u05d0\u05e4\u05e1. \u05d4\u05d7\u05d9\u05e9\u05d5\u05d1 \u05d0\u05d9\u05e0\u05d5 \u05de\u05e9\u05e0\u05d4 \u05d0\u05ea \u05e6\u05d9\u05d5\u05df \u05d4\u05e8\u05d0\u05d9\u05d5\u05ea.</p></div></section>`;
  };
})();
