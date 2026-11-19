// Isolated BIO notification delivery. No collector, schedule, database recovery or trading policy.
const ORIGIN='https://mozes2024.github.io', REPO='Mozes2024/mozes-biotech-intelligence';
const encoder=new TextEncoder();
const bytes=x=>typeof x==='string'?encoder.encode(x):new Uint8Array(x);
const join=(...xs)=>{const out=new Uint8Array(xs.reduce((n,x)=>n+x.length,0));let i=0;for(const x of xs){out.set(x,i);i+=x.length}return out};
const b64=x=>btoa(String.fromCharCode(...new Uint8Array(x))).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_');
const un64=x=>Uint8Array.from(atob(x.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
const json=x=>new Response(JSON.stringify(x),{headers:{'Content-Type':'application/json','Cache-Control':'no-store','Access-Control-Allow-Origin':ORIGIN,'Vary':'Origin'}});
const hash=async x=>b64(await crypto.subtle.digest('SHA-256',bytes(x)));
function eligible(a){return a.popup_eligible===true&&a.issuer_verified===true&&a.confirmed_outcome===true&&a.needs_review===false&&a.semantic_version==='alert-evidence-v1'&&['source_text','feed_summary','structured_source'].includes(a.evidence_basis)&&['P1','P2'].includes(a.priority)&&({fda_decision:['approval','crl','pdufa_extension','refusal_to_file'],material_safety:['clinical_hold','hold_lifted'],clinical_outcome:['primary_endpoint_met','primary_endpoint_failed','primary_not_significant','futility','trial_discontinued'],market_halt:['halt','resumption','volatility_pause']}[a.event_family]||[]).includes(a.event_outcome)&&!a.historical_only}
async function producer(request){
 const token=(request.headers.get('Authorization')||'').replace(/^Bearer /,'');const parts=token.split('.');if(parts.length!==3)throw Error('unauthorized');
 const h=JSON.parse(new TextDecoder().decode(un64(parts[0]))),p=JSON.parse(new TextDecoder().decode(un64(parts[1])));const now=Date.now()/1000;
 if(h.alg!=='RS256'||p.iss!=='https://token.actions.githubusercontent.com'||p.repository!==REPO||p.ref!=='refs/heads/main'||p.workflow_ref!==REPO+'/.github/workflows/lightweight-monitor.yml@refs/heads/main'||p.aud!=='mozes-bio-push'||![p.exp,p.nbf,p.iat].every(Number.isFinite)||p.exp<now||p.nbf>now+30||p.iat<now-600||p.iat>now+30||!Number.isSafeInteger(Number(p.run_number))||Number(p.run_number)<1)throw Error('unauthorized');
 const r=await fetch('https://token.actions.githubusercontent.com/.well-known/jwks');if(!r.ok)throw Error('identity-unavailable');const keys=await r.json(),j=keys.keys.find(k=>k.kid===h.kid);if(!j)throw Error('unauthorized');
 const k=await crypto.subtle.importKey('jwk',j,{name:'RSASSA-PKCS1-v1_5',hash:'SHA-256'},false,['verify']);if(!await crypto.subtle.verify('RSASSA-PKCS1-v1_5',k,un64(parts[2]),bytes(parts[0]+'.'+parts[1])))throw Error('unauthorized');return p;
}
async function hkdf(secret,salt,info,length){const key=await crypto.subtle.importKey('raw',secret,'HKDF',false,['deriveBits']);return new Uint8Array(await crypto.subtle.deriveBits({name:'HKDF',hash:'SHA-256',salt,info},key,length*8))}
async function encrypted(subscription,payload){
 const pair=await crypto.subtle.generateKey({name:'ECDH',namedCurve:'P-256'},true,['deriveBits']);const pub=new Uint8Array(await crypto.subtle.exportKey('raw',pair.publicKey)),client=un64(subscription.keys.p256dh);
 const remote=await crypto.subtle.importKey('raw',client,{name:'ECDH',namedCurve:'P-256'},false,[]);const shared=await crypto.subtle.deriveBits({name:'ECDH',public:remote},pair.privateKey,256);
 const ikm=await hkdf(shared,un64(subscription.keys.auth),join(bytes('WebPush: info\0'),client,pub),32),salt=crypto.getRandomValues(new Uint8Array(16));
 const key=await hkdf(ikm,salt,bytes('Content-Encoding: aes128gcm\0'),16),nonce=await hkdf(ikm,salt,bytes('Content-Encoding: nonce\0'),12);
 const aes=await crypto.subtle.importKey('raw',key,'AES-GCM',false,['encrypt']);const cipher=await crypto.subtle.encrypt({name:'AES-GCM',iv:nonce},aes,join(bytes(JSON.stringify(payload)),new Uint8Array([2])));
 return join(salt,new Uint8Array([0,0,16,0,65]),pub,new Uint8Array(cipher));
}
async function sendPush(env,sub,payload){
 const privateJwk=await env.PUSH.get('vapid','json');if(!privateJwk)throw Error('not-initialized');const pub=b64(join(new Uint8Array([4]),un64(privateJwk.x),un64(privateJwk.y)));
 const header=b64(bytes(JSON.stringify({typ:'JWT',alg:'ES256'}))),claims=b64(bytes(JSON.stringify({aud:new URL(sub.endpoint).origin,exp:Math.floor(Date.now()/1000)+3600,sub:'https://mozes2024.github.io/mozes-biotech-intelligence/'})));
 const key=await crypto.subtle.importKey('jwk',privateJwk,{name:'ECDSA',namedCurve:'P-256'},false,['sign']);const signature=b64(await crypto.subtle.sign({name:'ECDSA',hash:'SHA-256'},key,bytes(header+'.'+claims)));
 const body=await encrypted(sub,payload);const r=await fetch(sub.endpoint,{method:'POST',headers:{Authorization:'vapid t='+header+'.'+claims+'.'+signature+', k='+pub,'Content-Encoding':'aes128gcm','Content-Type':'application/octet-stream',TTL:'600',Urgency:'high'},body,redirect:'error'});return r.status;
}
async function body(request,limit=200000){const text=await request.text();if(text.length>limit)throw Error('too-large');return JSON.parse(text)}
function subscriptionValid(s){try{const u=new URL(s.endpoint);return u.protocol==='https:'&&!u.username&&!u.password&&['fcm.googleapis.com','updates.push.services.mozilla.com','web.push.apple.com'].includes(u.hostname)&&un64(s.keys.p256dh).length===65&&un64(s.keys.auth).length===16}catch(_){return false}}
async function handle(request,env){
 try{
 const u=new URL(request.url),path=u.pathname;
 if(request.method==='OPTIONS')return new Response(null,{headers:{'Access-Control-Allow-Origin':ORIGIN,'Access-Control-Allow-Methods':'GET, POST, OPTIONS','Access-Control-Allow-Headers':'Content-Type','Access-Control-Max-Age':'600'}});
 if(path==='/health')return json({service:'bio-desktop-push',version:1});
 if(path==='/key'&&request.method==='GET'){const j=await env.PUSH.get('vapid','json');return json(j?{publicKey:b64(join(new Uint8Array([4]),un64(j.x),un64(j.y)))}:{error:'initializing'})}
 if(path==='/publish'&&request.method==='POST'){
  const identity=await producer(request),feed=await body(request);if(!Array.isArray(feed.alerts)||!Number.isFinite(Date.parse(feed.generated_at))||Math.abs(Date.now()-Date.parse(feed.generated_at))>900000)throw Error('invalid-feed');
  const previous=await env.PUSH.get('producer','json'),sequence=Number(identity.run_number);if(previous&&sequence<previous.sequence)return json({status:'already-observed'});
  if(!await env.PUSH.get('vapid')){const pair=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},true,['sign','verify']);await env.PUSH.put('vapid',JSON.stringify(await crypto.subtle.exportKey('jwk',pair.privateKey)))}
  // Single serialized producer; no historical replay. First publication only establishes baseline.
  const old=new Set(previous?.ids||[]),fresh=previous&&sequence>previous.sequence?feed.alerts.filter(a=>eligible(a)&&!old.has(a.change_id)&&Date.parse(a.created_at)>Date.now()-3600000&&Date.parse(a.created_at)<=Date.now()+30000):[];
  const device=await env.PUSH.get('device','json'),pending=new Map((previous?.pending||[]).filter(a=>Date.parse(a.created_at)>Date.now()-3600000).map(a=>[a.change_id,a]));
  // Enrollment is a new baseline. Never queue events for a future device.
  if(device)for(const a of fresh)if(Date.parse(a.created_at)>device.created)pending.set(a.change_id,a);
  const state={sequence,ids:feed.alerts.map(a=>a.change_id).slice(0,200),updated_at:Date.now(),pending:[...pending.values()].slice(-20)};
  await env.PUSH.put('producer',JSON.stringify(state));
  let accepted=0,failed=0;
  if(device&&state.pending.length){
   const batch=state.pending,first=batch[0],id=batch.length===1?first.change_id:'batch-'+await hash(batch.map(a=>a.change_id).sort().join('|'));
   const status=await sendPush(env,device.subscription,{id,ids:batch.map(a=>a.change_id),title:batch.length===1?`MOZES ${first.priority} ${first.ticker}`:`MOZES - ${batch.length} אירועים מאומתים חדשים`,body:batch.length===1?String(first.headline||'אירוע מאומת חדש').slice(0,450):batch.map(a=>a.ticker).join(', ').slice(0,300)+' - פתח את רשימת ההתראות לפרטים.',url:ORIGIN+'/mozes-biotech-intelligence/#alerts',created_at:first.created_at});
   if(status===404||status===410){await env.PUSH.delete('device');state.pending=[]}
   else if(status>=200&&status<300){accepted=batch.length;state.pending=[]}else failed=batch.length;
  }
  else state.pending=[];
  await env.PUSH.put('producer',JSON.stringify(state));
  return json({status:'observed',eligible_new:fresh.length,push_accepted:accepted,push_failed:failed,enrolled:!!device,pending:state.pending.length});
 }
 if(path==='/enroll'&&request.method==='POST'){
  if(request.headers.get('Origin')!==ORIGIN)throw Error('unauthorized');const x=await body(request,6000),saved=await env.PUSH.get('enrollment','json'),config=saved||(env.ENROLLMENT_HASH&&env.ENROLLMENT_EXPIRES?{hash:env.ENROLLMENT_HASH,expires:Number(env.ENROLLMENT_EXPIRES),used:false}:null);
  if(!config||config.expires<Date.now()||config.used||await hash(x.code||'')!==config.hash||!subscriptionValid(x.subscription))throw Error('invalid-enrollment');
  if(await env.PUSH.get('device'))throw Error('already-enrolled');const manage=b64(crypto.getRandomValues(new Uint8Array(24)));await env.PUSH.put('enrollment',JSON.stringify({...config,used:true}));
  await env.PUSH.put('device',JSON.stringify({subscription:x.subscription,manageHash:await hash(manage),created:Date.now()}));return json({enrolled:true,manage});
 }
 if((path==='/test'||path==='/unsubscribe')&&request.method==='POST'){
  if(request.headers.get('Origin')!==ORIGIN)throw Error('unauthorized');const x=await body(request,1000),device=await env.PUSH.get('device','json');if(!device||await hash(x.manage||'')!==device.manageHash)throw Error('unauthorized');
  if(path==='/unsubscribe'){await env.PUSH.delete('device');return json({unsubscribed:true})}
  if(Date.now()-(device.lastTest||0)<60000)throw Error('test-rate-limited');await env.PUSH.put('device',JSON.stringify({...device,lastTest:Date.now()}));
  const status=await sendPush(env,device.subscription,{id:'test-'+Date.now(),title:'MOZES - בדיקת תצוגה בלבד',body:'זוהי בדיקת חיבור ותצוגה, לא אירוע שוק ולא הוכחה למסירת אירועים אמיתיים.',url:ORIGIN+'/mozes-biotech-intelligence/#alerts'});return json({push_accepted:status>=200&&status<300,status});
 }
 return new Response('Not found',{status:404});
 }catch(e){return json({error:['unauthorized','invalid-enrollment','already-enrolled','test-rate-limited'].includes(e.message)?e.message:'delivery-unavailable'})}
}
export {eligible,encrypted,subscriptionValid,producer,handle};

// One owner, one serialized state machine. Storage is SQLite-backed and strongly consistent.
export class PushDelivery {
 constructor(ctx,env){this.ctx=ctx;this.env=env;this.tail=Promise.resolve()}
 fetch(request){
  const run=()=>handle(request,{...this.env,PUSH:{
   get:async(k,type)=>{const value=await this.ctx.storage.get(k);return value===undefined?null:type==='json'?JSON.parse(value):value},
   put:(k,value)=>this.ctx.storage.put(k,value),delete:k=>this.ctx.storage.delete(k)
  }});
  const result=this.tail.then(run,run);this.tail=result.then(()=>{},()=>{});return result;
 }
}
export default {async fetch(request,env){
 const path=new URL(request.url).pathname;
 if(path==='/health')return json({service:'bio-desktop-push',version:2});
 if(!['/key','/publish','/enroll','/test','/unsubscribe'].includes(path))return new Response('Not found',{status:404});
 return env.DELIVERY.get(env.DELIVERY.idFromName('owner')).fetch(request);
}};
