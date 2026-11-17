// Opt-in for the owner's Brave desktop, independent from page visibility polling.
const BIO_PUSH='https://mozes-bio-push.sp500.workers.dev';
let bioPushMessage='';
function bioPushStatus(){
 const active=!!localStorage.getItem('mozes-bio-push-manage');
 return `<div class="notice"><b>התראות למחשב גם בלשונית אחרת</b><p>${active?'הדפדפן רשום לשליחה ברקע. הרשמה אינה הוכחת מסירה של אירוע אמיתי.':'נדרשת הרשמה פרטית מהקישור החד-פעמי. הרשאת דפדפן לבדה אינה מפעילה את השליחה.'}</p><p>נשלחים רק אירועים חדשים שעברו אימות. לא נשלחות מחדש התראות ישנות. Brave, מערכת ההפעלה, מצב שינה או חיבור רשת עלולים לעכב מסירה.</p><p>${typeof esc==='function'?esc(bioPushMessage):''}</p>${active?'<button class="btn" onclick="bioPushTest()">בדיקת תצוגה בעוד 8 שניות</button> <button class="btn" onclick="bioPushUnsubscribe()">בטל הרשמה ומחק נתוני דפדפן מהשרת</button>':'<button class="btn" onclick="bioPushEnroll()">רישום פרטי להתראות</button>'}</div>`;
}
const originalAlertsPage=alertsPage;
alertsPage=function(){return bioPushStatus()+originalAlertsPage()};
function bioPushCode(){const q=new URLSearchParams(location.hash.replace(/^#/,''));return q.get('bio-enroll')}
async function bioPushCall(path,value){const r=await fetch(BIO_PUSH+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});const x=await r.json();if(!r.ok||x.error)throw Error(x.error||'delivery-unavailable');return x}
async function bioPushEnroll(){
 try{
  if(!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window))throw Error('unsupported');
  const code=bioPushCode();if(!code)throw Error('enrollment-link-required');
  const permission=await Notification.requestPermission();if(permission!=='granted')throw Error('permission-required');
  const registration=await navigator.serviceWorker.register('bio-push-sw.js',{scope:'./'});await navigator.serviceWorker.ready;
  const {publicKey}=await (await fetch(BIO_PUSH+'/key',{cache:'no-store'})).json();if(!publicKey)throw Error('initializing');
  const key=Uint8Array.from(atob(publicKey.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
  const subscription=await registration.pushManager.getSubscription()||await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
  const result=await bioPushCall('/enroll',{code,subscription:subscription.toJSON()});localStorage.setItem('mozes-bio-push-manage',result.manage);
  history.replaceState(null,'',location.pathname+'#alerts');bioPushMessage='ההרשמה נשמרה. לחץ על בדיקת תצוגה, ואז עבור ללשונית אחרת. הבדיקה אינה אירוע שוק.';page='alerts';render();
 }catch(e){bioPushMessage=({'enrollment-link-required':'פתח את קישור ההרשמה הפרטי שקיבלת.','permission-required':'נדרשת הרשאת התראות לדפדפן.','unsupported':'הדפדפן אינו תומך בהרשמה זו.','invalid-enrollment':'קישור ההרשמה אינו תקף או שכבר נוצל.','already-enrolled':'כבר קיימת הרשמת מחשב. אין להחליף אותה ללא ביטול מסודר.','initializing':'השירות עדיין בהכנה.'})[e.message]||'ההרשמה לא הושלמה. ב-Brave בדוק שהאפשרות Use Google Services for Push Messaging פעילה ושהאתר רשאי להציג התראות.';render()}
}
async function bioPushTest(){bioPushMessage='עבור כעת ללשונית אחרת. בדיקת תצוגה בלבד תישלח בעוד 8 שניות; זו אינה התראת שוק.';render();await new Promise(resolve=>setTimeout(resolve,8000));try{const x=await bioPushCall('/test',{manage:localStorage.getItem('mozes-bio-push-manage')});bioPushMessage=x.push_accepted?'שירות הדחיפה קיבל את בדיקת התצוגה. רק הופעה במחשב שלך מוכיחה תצוגה; זו אינה הוכחת מסירת אירוע שוק.':'שירות הדחיפה לא קיבל את הבדיקה.'}catch(_){bioPushMessage='בדיקת התצוגה לא נשלחה. נסה שוב לאחר דקה.'}render()}
async function bioPushUnsubscribe(){try{await bioPushCall('/unsubscribe',{manage:localStorage.getItem('mozes-bio-push-manage')});const r=await navigator.serviceWorker.getRegistration();const s=await r?.pushManager.getSubscription();await s?.unsubscribe();localStorage.removeItem('mozes-bio-push-manage');bioPushMessage='ההרשמה בוטלה ונתוני ההרשמה נמחקו מהשרת.'}catch(_){bioPushMessage='לא ניתן לאשר את ביטול ההרשמה. נסה שוב; אפשר לחסום התראות בהגדרות הדפדפן.'}render()}
if(bioPushCode()){page='alerts';setTimeout(()=>{page='alerts';render()},1000)}
