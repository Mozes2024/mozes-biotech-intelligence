/* MOZES Live Intelligence v2A UI overlay.
 * Loaded after app.js so it can enrich cards and drawer content without duplicating
 * the core application. All signals are descriptive and do not change recommendations.
 */
(function(){
  const baseFocusCard = focusCard;
  const baseMarket = market;
  const baseDashboard = dashboard;

  function pctx(x){ return x==null ? '—' : `${(x*100).toFixed(1)}%`; }
  function numx(x){ return x==null ? '—' : Number(x).toFixed(1); }
  function eventTypeHe(t){ return (HE.type||{})[t] || t || 'אירוע'; }

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

  focusCard = function(a){
    let html=baseFocusCard(a);
    const mi=a.market_intelligence||{};
    let extra='';
    if(mi.available){
      const chase=mi.post_event_chase_risk?`<div class="notice red-note" style="margin:9px 0 0;padding:8px"><b>⚠ מהלך חד כבר קרה.</b> ${mi.recent_return_7d!=null?`7 ימים: ${esc(pctx(mi.recent_return_7d))}. `:''}המשך מעקב ≠ נקודת כניסה חדשה.</div>`:'';
      extra+=`<div class="simple-signals" style="margin-top:8px">${simpleSignal('תשומת לב שוק',mi.attention_label_he||'—')}${simpleSignal('הקשר לתנועה',mi.context_label_he||'—')}${mi.relative_volume_20d!=null?simpleSignal('מחזור מול 20 יום',`${numx(mi.relative_volume_20d)}x`):''}</div>${chase}`;
    }
    extra+=recentCatalystHtml(a)+chainHtml(a);
    return html.replace('<div class="focus-open">',extra+'<div class="focus-open">');
  };

  market = function(a){
    let html=baseMarket(a);
    const mi=a.market_intelligence||{};
    if(!mi.available)return html;
    const warn=mi.post_event_chase_risk?`<div class="notice red-note" style="margin-top:10px"><b>מהלך חד כבר התרחש:</b> המערכת משאירה את המניה במעקב בגלל קטליזטורים עתידיים, אך הסיגנל הזה מזהיר מפני בלבול בין “אירוע מעניין” לבין “כניסה אחרי זינוק”.</div>`:'';
    const chain=chainHtml(a);
    return html+`<div class="section"><h4>Live Intelligence</h4><div class="detail-grid"><div class="detail-box"><div class="k">תשומת לב שוק</div><div class="v">${esc(mi.attention_score)} · ${esc(mi.attention_label_he)}</div></div><div class="detail-box"><div class="k">מחזור יחסי</div><div class="v">${mi.relative_volume_20d==null?'—':esc(numx(mi.relative_volume_20d))+'x'}</div></div><div class="detail-box"><div class="k">הקשר</div><div class="v">${esc(mi.context_label_he||'—')}</div></div><div class="detail-box"><div class="k">7 ימים</div><div class="v">${esc(pctx(mi.recent_return_7d))}</div></div><div class="detail-box"><div class="k">30 ימים</div><div class="v">${esc(pctx(mi.recent_return_30d))}</div></div><div class="detail-box"><div class="k">מול XBI, 7 ימים</div><div class="v">${esc(pctx(mi.relative_return_7d_vs_xbi))}</div></div></div>${warn}${recentCatalystHtml(a)}${chain}<div class="company" style="margin-top:8px">${esc(mi.note_he||'')}</div></div>`;
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
    return pageTitle('מה מעניין עכשיו?','מסך פשוט שמרכז מניות — לא שורות אירוע כפולות — ומבדיל בין אירוע שכבר קרה לקטליזטור הבא',`עודכן ${esc(D.generated_at?.replace('T',' ').slice(0,16))}`)+`<div class="decision-banner ${modelOpen?'open':'locked'}"><div><b>${modelOpen?'המודל מאפשר כבר מועמדויות מתקדמות':'המלצות מסחר עדיין נעולות'}</b><span>${modelOpen?'מניות שיעברו את התנאים יכולות להגיע לשלב מתקדם.':'עד שנוכיח יתרון אמיתי על נתונים נקיים, המערכת מסמנת מה שווה לבדוק — ולא אומרת לקנות.'}</span></div><span class="decision-lock">${modelOpen?'✓':'🔒'}</span></div><div class="simple-summary"><div class="summary-box green"><strong>${strong.length}</strong><span>מועמדות לבדיקה מעמיקה</span></div><div class="summary-box blue"><strong>${watch.length}</strong><span>למעקב</span></div><div class="summary-box red"><strong>${avoid.length}</strong><span>לא מתאימות כרגע</span></div><div class="summary-box slate"><strong>${verify.length}</strong><span>דורשות אימות</span></div></div><section class="panel simple-panel"><div class="panel-head"><div><h3>${strong.length?'המניות שהכי שווה לבדוק עכשיו':'המניות המעניינות ביותר כרגע'}</h3><p class="panel-sub">כל טיקר מופיע פעם אחת. אם אירוע גדול כבר התרחש, הכרטיס מציג זאת ומסביר איזה קטליזטור עתידי משאיר את המניה במעקב.${dedup?` אוחדו ${dedup} שורות אירוע כפולות לפי טיקר.`:''}</p></div><span class="spacer"></span><button class="btn" onclick="page='radar';render()">לכל האירועים</button></div><div class="focus-grid">${primary.map(focusCard).join('')||'<div class="empty">כרגע אין מניות שעומדות אפילו בתנאי המעקב הבסיסיים.</div>'}</div></section><section class="panel explainer"><div class="panel-head"><h3>איך לקרוא את הסימונים?</h3></div><div class="explain-grid"><div><span class="explain-dot green"></span><b>מועמדת לבדיקה מעמיקה</b><p>אירוע חשוב, מקור מאומת וראיות מספיק חזקות כדי להצדיק מחקר נוסף.</p></div><div><span class="explain-dot blue"></span><b>מעקב / מעניינת לבדיקה</b><p>יש קטליזטור עתידי, אבל חשוב לבדוק גם אם מהלך חד כבר התרחש.</p></div><div><span class="explain-dot red"></span><b>מהלך חד כבר קרה</b><p>זה דגל הקשר בלבד: לא מסיר את הקטליזטור, אך מונע בלבול בין מעקב לבין רדיפה אחרי זינוק.</p></div><div><span class="explain-dot slate"></span><b>חסר אימות</b><p>האירוע טרם אושר מספיק מול מקור ראשוני, ולכן לא מתקדם.</p></div></div></section>`;
  };
})();
