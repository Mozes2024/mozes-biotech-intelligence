// Alert transport is independent of the large research snapshot.
function createAlertNotifier({storage, show, system, lock=task=>task(), now=()=>Date.now()}){
  const key='mozes-alerts-seen-v1';let memory={};
  return {async observe(rows,baseline=false){return lock(()=>{
    let seen;try{seen=JSON.parse(storage.getItem(key)||'{}')}catch(_){seen=memory}
    if(!seen||typeof seen!=='object'||Array.isArray(seen))seen={};
    const stamp=now(),cutoff=stamp-7*86400000;
    seen=Object.fromEntries(Object.entries(seen).filter(([,at])=>Number.isFinite(at)&&at>=cutoff));
    const fresh=rows.filter(a=>a.change_id&&!seen[a.change_id]);
    for(const a of rows)if(a.change_id)seen[a.change_id]=stamp;
    memory=seen;try{storage.setItem(key,JSON.stringify(seen))}catch(_){}
    if(!baseline&&fresh.length){show(fresh);for(const a of fresh)system(a)}
    return baseline?[]:fresh;
  })}};
}
let alertNotifier=null,lastAlertFeed=null,alertFeedInitialized=false,alertPollBusy=false,preferredAlertFeedUrl=null;
function getAlertNotifier(){
  if(!alertNotifier){
    let storage;try{storage=window.localStorage}catch(_){storage={getItem:()=>null,setItem:()=>{}}}
    alertNotifier=createAlertNotifier({storage,show:showAlertPopup,system:showSystemAlert,
      lock:task=>navigator.locks?navigator.locks.request('mozes-alert-notifications',task):task()});
  }
  return alertNotifier;
}
function showAlertPopup(rows){
  const box=$('alertPopups');if(!box)return;
  box.innerHTML=`<div class="alert-popup-head"><b>${rows.length===1?'התראה חדשה':`${rows.length} התראות חדשות`}</b><button class="btn" type="button" aria-label="סגור התראות" data-dismiss>×</button></div>`+
    rows.slice(0,3).map(a=>`<div class="alert-popup-row"><b><span class="ltr">${esc(a.ticker||'—')}</span> · ${esc(a.priority)}</b><p>${esc(a.headline||alertWhyHe(a))}</p></div>`).join('')+
    `<button class="btn primary" type="button" data-open-alerts>פתח את ההתראות</button>`;
  box.hidden=false;
  box.querySelector('[data-dismiss]').onclick=()=>{box.hidden=true};
  box.querySelector('[data-open-alerts]').onclick=()=>{box.hidden=true;page='alerts';render()};
}
function showSystemAlert(a){
  if(!('Notification' in window)||Notification.permission!=='granted')return;
  try{const n=new Notification(`MOZES ${a.priority||''} ${a.ticker||''}`.trim(),{
    body:a.headline||alertWhyHe(a),lang:'he',dir:'rtl',tag:a.change_id});
    n.onclick=()=>{window.focus();page='alerts';render();n.close()};
  }catch(_){}
}
function alertDeliveryNotice(){
  const enabled=D?.alert_config?.enabled_channels||[];
  const text=enabled.includes('email')?'ערוץ האימייל מוגדר. מצב השליחה מופיע בכל התראה.':
    'ערוץ האימייל אינו מוגדר בניטור האחרון שדווח, או שמצב ההגדרה עדיין לא ידוע.';
  return text+' התראות חדשות מוצגות כאן כשהאתר פתוח. הכיוון הוא סיווג טקסט אוטומטי ואינו תחזית מחיר.';
}
function alertDeliveryHe(a){
  const email=a.delivery?.email;
  if(!email)return 'אימייל: לא נשלח בערוץ זה';
  const state={pending:'ממתין לשליחה',sending:'בשליחה',sent:'התקבל אצל שרת הדואר',failed:'השליחה נכשלה; יבוצע ניסיון חוזר',dead:'השליחה הופסקה'};
  return 'אימייל: '+(state[email.status]||'מצב לא ידוע');
}
function alertAnalysisHtml(a){
  const x=a.explanation;
  if(!x)return '<div class="company">הסבר לפי המקור: ממתין לקריאת תוכן ההודעה.</div>';
  const basis={source_text:'תוכן המקור נקרא',feed_summary:'נקרא תקציר ההודעה בלבד',headline_only:'כותרת בלבד',structured_source:'נתונים מובנים מהמקור'};
  return `<div class="company"><b>בסיס ההסבר:</b> ${esc(basis[x.basis]||'לא ידוע')}${x.ai_status==='used'?' · ניסוח בסיוע מודל שפה':''}</div>`+
    (x.evidence||[]).map(e=>`<blockquote class="alert-evidence" dir="auto">${esc(e.quote)}</blockquote>`).join('')+
    (x.missing_he||[]).map(text=>`<div class="company">${esc(text)}</div>`).join('');
}
function safeSourceUrl(value){
  try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null}catch(_){return null}
}
function preserveAlertFeed(next){
  if(lastAlertFeed){next.alerts=lastAlertFeed.alerts;next.alert_config=lastAlertFeed.alert_config}
}
async function checkForAlerts(){
  if(!D||document.hidden||alertPollBusy)return;
  alertPollBusy=true;
  try{
    if(D.alert_config?.feed_url)preferredAlertFeedUrl=D.alert_config.feed_url;
    const url=apiMode?'/api/alerts':preferredAlertFeedUrl||'alerts.json';
    let r;
    try{r=await fetch(url,{cache:'no-store'});if(!r.ok)throw Error(r.status)}
    catch(error){if(apiMode||url==='alerts.json')throw error;r=await fetch('alerts.json',{cache:'no-store'})}
    if(!r.ok)throw Error(r.status);
    const feed=await r.json();
    if(feed.schema!==1||!Array.isArray(feed.alerts)||typeof feed.revision!=='string')throw Error('Invalid alert feed');
    if(lastAlertFeed&&Date.parse(feed.generated_at)<Date.parse(lastAlertFeed.generated_at))return;
    if(lastAlertFeed?.revision===feed.revision){lastAlertFeed=feed;return}
    await getAlertNotifier().observe(feed.alerts,!alertFeedInitialized);
    alertFeedInitialized=true;lastAlertFeed=feed;preserveAlertFeed(D);render();
  }finally{alertPollBusy=false}
}
