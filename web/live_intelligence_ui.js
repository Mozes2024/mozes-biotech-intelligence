/* MOZES Live Intelligence v2B UI overlay.
 * Loaded after app.js. It enriches cards/drawer with fresh-market context, catalyst
 * continuity and system-health cues without changing clinical/regulatory evidence.
 */
(function(){
  const baseDecisionStatus = decisionStatus;
  const baseFocusCard = focusCard;
  const baseMarket = market;

  decisionStatus = function(a){
    if(a?.recommendation?.status==='HIGH_RESEARCH_PRIORITY'){
      return {key:'strong',label:a.recommendation.label_he||'עדיפות מחקר גבוהה',tone:'green',rank:0};
    }
    return baseDecisionStatus(a);
  };

  function pctx(x){ return x==null ? '—' : `${(x*100).toFixed(1)}%`; }
  function numx(x){ return x==null ? '—' : Number(x).toFixed(1); }
  function eventTypeHe(t){ return (HE.type||{})[t] || t || 'אירוע'; }

  function healthBanner(){
    // Technical health stays at the bottom of the home page, collapsed by default.
    const h=D?.health||{};
    const checked=h.latest_monitor?.finished_at;
    const checkedDate=checked?new Date(checked):null;
    const age=checkedDate&&!isNaN(checkedDate)?Math.max(0,Math.floor((Date.now()-checkedDate.getTime())/60000)):null;
    const s=h.severity||{};
    const primary=h.primary_catalyst_monitoring||{};
    const red=s.red||[], amber=s.amber||[], info=s.info||[];
    const details=[...red,...amber,...info].map(x=>`${esc(x.module||'module')}: ${esc(x.status||'INFO')}`).join(' · ');
    const latency=D?.hot_latency||{};
    const fmt=x=>x==null?'N/A':`${Number(x).toFixed(1)} שנ׳`;
    const groups=latency.by_source_type||{};
    const metric=group=>group?.metrics?.publication_to_first_seen||{};
    const sourceRow=(label,types)=>{const samples=types.map(t=>metric(groups[t])).filter(x=>x.n);
      const row=samples.length===1?samples[0]:null;
      return `<div>${label}: p50 ${fmt(row?.p50)} · p95 ${fmt(row?.p95)} · n=${row?.n??0}</div>`;};
    const total=latency.metrics?.publication_to_sent||{};
    const timing=`${sourceRow('SEC',['sec'])}${sourceRow('FDA',['fda'])}${sourceRow('Wire',['wire'])}${sourceRow('IR',['company_ir'])}<div>מקור עד שליחה: p50 ${fmt(total.p50)} · p95 ${fmt(total.p95)} · n=${total.n??0}</div>`;
    const freshness=(h.source_freshness||[]).map(x=>`<div>${esc(x.source)}: ${esc(x.status)} · בדיקה ${esc(x.last_checked_at||'N/A')} · הצלחה ${esc(x.last_success_at||'N/A')} · שגיאה ${esc(x.last_error_at||'N/A')} · כשלים רצופים ${esc(x.consecutive_failures)}</div>`).join('');
    return `<details class="notice ${red.length?'red-note':'blue-note'}" style="margin-top:18px"><summary id="sourceFreshness" data-checked="${esc(checked||'')}"><b>סטטוס מערכת</b> · בדיקת מקורות ${age===null?'לא זמינה':`לפני ${age} דקות`} · ${esc(primary.label_he||'ניטור ליבה')} · SEC ${h.sec_monitoring_enabled?'פעיל':'כבוי'}</summary><div class="company" style="margin-top:8px">${timing}${freshness}${details?`פרטים: ${details}`:''}</div></details>`;
  }

  function recentCatalystHtml(a){
    const r=a.recent_completed_catalyst;
    if(!r)return '';
    const move=r.return_since_event==null?'':` · המניה ${r.return_since_event>=0?'עלתה':'ירדה'} ${Math.abs(r.return_since_event*100).toFixed(1)}% מאז`;
    return `<div class="notice ${r.return_since_event!=null&&r.return_since_event>0.5?'red-note':'blue-note'}" style="margin-top:10px"><b>אירוע קודם כבר קרה:</b> ${esc(eventTypeHe(r.catalyst_type))} · לפני ${esc(r.days_since)} ימים${esc(move)}.<br><span class="company">הטיקר נשאר כאן בגלל קטליזטור עתידי אחר — לא בגלל האירוע שכבר פורסם.</span></div>`;
  }

  function chainHtml(a){
    const chain=(a.catalyst_chain||[]);
    if(chain.length<2)return '';
    return `<div style="margin-top:9px;border-top:1px dashed #e2e8f0;padding-top:8px"><div class="company"><b>שרשרת קטליזטורים:</b> ${chain.map(x=>`${esc(eventTypeHe(x.type))}${x.start?' '+esc(x.start):''}`).join(' ← ')}</div></div>`;
  }

  function eventEvidenceHtml(a){
    const trigger=a.timing_mode==='EVENT_DRIVEN';
    const progress=a.trigger_current!=null&&a.trigger_target!=null?`${esc(a.trigger_current)}/${esc(a.trigger_target)}`:'';
    const window=a.date?.window;
    const next=trigger?(progress||'ממתין לטריגר מאומת'):(window?.start?`${esc(window.start)}${window.end&&window.end!==window.start?'–'+esc(window.end):''}`:'חלון טרם אומת');
    const quality=esc(a.state?.verification_state||'טרם אומת');
    const links=(a.sources||[]).map(s=>`<div>${esc(s.source_type||'מקור')} · ${esc(s.published_at||'')} ${s.url?`<a href="${esc(s.url)}" target="_blank" rel="noreferrer">מקור ↗</a>`:''}</div>`).join('');
    return `<div class="event-evidence"><span class="event-chip">${esc(eventTypeHe(a.type))}</span>${trigger?'<span class="event-chip event-driven">EVENT DRIVEN</span>':''}<div>הטריגר הבא: <b>${next}</b></div><div>אימות מקור: <b>${quality}</b>${a.mapping_basis?` · מיפוי ${esc(a.mapping_basis)}`:''}</div><details onclick="event.stopPropagation()"><summary>מקורות ואסמכתאות</summary>${links||'אין מקורות מקושרים'}</details></div>`;
  }

  function coverageHtml(){
    const c=D?.coverage||{};
    return `<div class="coverage-strip"><b>כיסוי מקורות</b> · ${esc(c.scanned_candidates??0)} מועמדים נבדקו · ${esc(c.total_evidence??0)} ראיות מקור · ${esc(c.review_required_count??0)} דורשים בדיקה${c.truncated?' · הסריקה חלקית':''}</div>`;
  }

  function changesTop(){
    const rows=(D?.changes||[]).slice(0,3);
    return rows.length?`<section class="panel"><div class="panel-head"><h3>מה השתנה?</h3><span class="spacer"></span><button class="btn" onclick="page='changes';render()">כל השינויים</button></div><div class="panel-body">${changeRows(rows)}</div></section>`:'';
  }

  changedCompact = function(){ return ''; };
  // alertsCompact is inlined at the top of dashboard(); avoid a duplicate block at the end.
  const baseAlertsCompact = typeof alertsCompact === 'function' ? alertsCompact : function(){ return ''; };
  alertsCompact = function(){ return ''; };

  function freshnessHtml(mi){
    if(!mi?.available)return '';
    if(!mi.price_fresh){
      return `<div class="notice red-note" style="margin-top:8px;padding:8px"><b>מחיר לא טרי:</b> התצפית האחרונה היא ${esc(mi.price_date||'—')} (${esc(mi.price_age_days??'—')} ימים). מדד תשומת הלב לא מוצג כ-live.</div>`;
    }
    if(mi.session_in_progress){
      return `<div class="notice blue-note" style="margin-top:8px;padding:8px"><b>המסחר עדיין פתוח:</b> שינוי המחיר יכול להיות תוך-יומי; מחזור יחסי מושבת עד בר יומי מלא.</div>`;
    }
    return '';
  }

  focusCard = function(a){
    let html=baseFocusCard(a);
    const mi=a.market_intelligence||{};
    let extra='';
    if(mi.available){
      const chase=mi.post_event_chase_risk?`<div class="notice red-note" style="margin:9px 0 0;padding:8px"><b>⚠ מהלך חד כבר קרה.</b> ${mi.recent_return_7d!=null?`7 ימים: ${esc(pctx(mi.recent_return_7d))}. `:''}המשך מעקב ≠ נקודת כניסה חדשה.</div>`:'';
      extra+=`<div class="simple-signals" style="margin-top:8px">${simpleSignal('תשומת לב שוק',mi.attention_label_he||'—')}${simpleSignal('הקשר לתנועה',mi.context_label_he||'—')}${mi.relative_volume_20d!=null?simpleSignal('מחזור מול 20 יום',`${numx(mi.relative_volume_20d)}x`):''}${mi.same_direction_big_move_share!=null?simpleSignal('רוחב תנועה בענף',pctx(mi.same_direction_big_move_share)):''}</div>${freshnessHtml(mi)}${chase}`;
    }
    extra+=eventEvidenceHtml(a)+recentCatalystHtml(a)+chainHtml(a);
    return html.replace('<div class="focus-open">',extra+'<div class="focus-open">');
  };

  market = function(a){
    let html=baseMarket(a);
    const mi=a.market_intelligence||{};
    if(!mi.available)return html;
    const warn=mi.post_event_chase_risk?`<div class="notice red-note" style="margin-top:10px"><b>מהלך חד כבר התרחש:</b> המערכת משאירה את המניה במעקב בגלל קטליזטורים עתידיים, אך הסיגנל הזה מונע בלבול בין “אירוע מעניין” לבין “כניסה אחרי זינוק”.</div>`:'';
    return html+`<div class="section"><h4>Live Intelligence</h4><div class="detail-grid"><div class="detail-box"><div class="k">תשומת לב שוק</div><div class="v">${mi.attention_score==null?'—':esc(mi.attention_score)} · ${esc(mi.attention_label_he||'—')}</div></div><div class="detail-box"><div class="k">מחיר אחרון</div><div class="v">${esc(mi.price_date||'—')}</div></div><div class="detail-box"><div class="k">מחזור יחסי</div><div class="v">${mi.relative_volume_20d==null?'—':esc(numx(mi.relative_volume_20d))+'x'}</div></div><div class="detail-box"><div class="k">הקשר</div><div class="v">${esc(mi.context_label_he||'—')}</div></div><div class="detail-box"><div class="k">רוחב תנועה</div><div class="v">${mi.same_direction_big_move_share==null?'—':esc(pctx(mi.same_direction_big_move_share))} · n=${esc(mi.breadth_sample_n??0)}</div></div><div class="detail-box"><div class="k">7 ימים</div><div class="v">${esc(pctx(mi.recent_return_7d))}</div></div><div class="detail-box"><div class="k">30 ימים</div><div class="v">${esc(pctx(mi.recent_return_30d))}</div></div><div class="detail-box"><div class="k">מול XBI, 7 ימים</div><div class="v">${esc(pctx(mi.relative_return_7d_vs_xbi))}</div></div></div>${freshnessHtml(mi)}${warn}${recentCatalystHtml(a)}${chainHtml(a)}<div class="company" style="margin-top:8px">${esc(mi.note_he||'')}</div></div>`;
  };

  dashboard = function(){
    let live=[...(D.live||[])];
    live.sort((a,b)=>{let da=decisionStatus(a),db=decisionStatus(b);return da.rank-db.rank||(b.evidence?.score||0)-(a.evidence?.score||0)||(b.impact?.score||0)-(a.impact?.score||0)});
    const unique=[]; const seen=new Set();
    for(const a of live){ if(!seen.has(a.ticker)){seen.add(a.ticker); unique.push(a);} }
    let strong=unique.filter(a=>['advanced','strong'].includes(decisionStatus(a).key));
    let watch=unique.filter(a=>['review','watch'].includes(decisionStatus(a).key));
    let avoid=unique.filter(a=>decisionStatus(a).key==='avoid');
    let verify=unique.filter(a=>decisionStatus(a).key==='verify');
    let modelOpen=D.validation?.hold_through?.satisfied||D.validation?.runup?.satisfied;
    let primary=(strong.length?strong:watch).slice(0,6);
    let dedup=live.length-unique.length;
    return pageTitle('מה מעניין עכשיו?','מסך מניות מאוחד שמבדיל בין אירוע שכבר קרה לקטליזטור הבא ומציג הקשר שוק טרי',`עודכן ${fmtLocal(D.generated_at)}`)+
      baseAlertsCompact()+
      `<div class="decision-banner ${modelOpen?'open':'locked'}"><div><b>${modelOpen?'שער אמפירי פתוח':'שערי המסחר האמפיריים עדיין נעולים'}</b><span>${modelOpen?'תוויות מתקדמות יכולות להישען גם על אימות אמפירי.':'גם setup חזק מקבל כרגע לכל היותר “עדיפות מחקר גבוהה”, לא “מועמדת להשקעה”.'}</span></div><span class="decision-lock">${modelOpen?'✓':'🔒'}</span></div><div class="simple-summary"><div class="summary-box green"><strong>${strong.length}</strong><span>עדיפות מחקר גבוהה</span></div><div class="summary-box blue"><strong>${watch.length}</strong><span>למעקב</span></div><div class="summary-box red"><strong>${avoid.length}</strong><span>לא מתאימות כרגע</span></div><div class="summary-box slate"><strong>${verify.length}</strong><span>דורשות אימות</span></div></div><section class="panel simple-panel"><div class="panel-head"><div><h3>${strong.length?'המניות שהכי שווה לבדוק עכשיו':'המניות המעניינות ביותר כרגע'}</h3><p class="panel-sub">כל טיקר מופיע פעם אחת. Market Attention הוא הקשר תיאורי בלבד ואינו משנה את הראיות הקליניות.${dedup?` אוחדו ${dedup} שורות אירוע כפולות לפי טיקר.`:''}</p></div><span class="spacer"></span><button class="btn" onclick="page='radar';render()">לכל האירועים</button></div><div class="focus-grid">${primary.map(focusCard).join('')||'<div class="empty">כרגע אין מניות שעומדות אפילו בתנאי המעקב הבסיסיים.</div>'}</div></section>`+
      changesTop()+coverageHtml()+
      `<section class="panel explainer"><div class="panel-head"><h3>איך לקרוא את הסימונים?</h3></div><div class="explain-grid"><div><span class="explain-dot green"></span><b>עדיפות מחקר גבוהה</b><p>Setup בולט שמצדיק מחקר עמוק; זו אינה תווית השקעה כל עוד השערים האמפיריים נעולים.</p></div><div><span class="explain-dot blue"></span><b>מעקב / מעניינת לבדיקה</b><p>יש קטליזטור עתידי, אבל חסר חלק מהתמונה.</p></div><div><span class="explain-dot red"></span><b>מהלך חד כבר קרה</b><p>דגל הקשר בלבד: מונע בלבול בין קטליזטור עתידי לבין רדיפה אחרי זינוק שכבר התרחש.</p></div><div><span class="explain-dot slate"></span><b>חסר אימות</b><p>האירוע טרם אושר מספיק מול מקור ראשוני.</p></div></div></section>`+
      healthBanner();
  };
})();
