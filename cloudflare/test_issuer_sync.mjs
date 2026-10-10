// Real isolated workerd D1, migrations 0001–0004; never contacts production.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {syncIssuers} from './src/index.js';

const mf=new Miniflare(convertV4MiniflareOptions({modules:true,
 script:'export default {fetch(){return new Response("offline")}}',
 compatibilityDate:'2026-08-01',d1Databases:{TEST:'test',BASELINE:'baseline'}}));
const universe=Array.from({length:310},(_,i)=>({cik:String(i+1),ticker:'BIO'+i,
 company:'Biotech '+i,confidence:.99,source:'SEC-v2C-equity'}));
const request=(rows,token='fixture')=>new Request('https://fixture/edge/sync',{
 method:'POST',headers:{Authorization:'Bearer '+token},body:JSON.stringify({issuers:rows})});
const totals={rows_read:0,rows_written:0,queries:0};
let fail=false;
try {
 const db=await mf.getD1Database('TEST'),old=await mf.getD1Database('BASELINE');
 for(const d of [db,old])for(const file of ['0001_alert_feed.sql','0002_hot_edge.sql',
 '0003_hot_edge_reliability.sql','0004_clinical_candidates.sql'])
  for(const sql of readFileSync(new URL('./migrations/'+file,import.meta.url),'utf8').split(';').filter(s=>s.trim()))
   await d.prepare(sql).run();
 const env={EDGE_SYNC_TOKEN:'fixture',ALERTS_DB:{prepare:q=>{
  const s=db.prepare(q);return {bind:(...values)=>s.bind(...values),async all(){const r=await s.all();
   totals.queries++;totals.rows_read+=r.meta.rows_read;totals.rows_written+=r.meta.rows_written;return r;}};
 },async batch(statements){
  if(fail)statements[2]=db.prepare('INSERT INTO missing_table VALUES(1)');
  const results=await db.batch(statements);
  for(const r of results){totals.queries++;totals.rows_read+=r.meta.rows_read;totals.rows_written+=r.meta.rows_written;}
  return results;
 }}};
 const rows=async()=> (await db.prepare('SELECT * FROM edge_issuers ORDER BY cik').all()).results;
 const delta=before=>Object.fromEntries(Object.keys(totals).map(k=>[k,totals[k]-before[k]]));
 const sync=async r=>{const response=await syncIssuers(request(r),env);assert.equal(response.status,200);return response.json();};
 let before={...totals};await sync(universe);const initialization=delta(before);
 assert.equal((await rows()).length,310);
 const original=await rows();before={...totals};
 for(let i=0;i<100;i++)assert.equal((await sync(universe)).unchanged,true);
 const unchanged100=delta(before);assert.equal(unchanged100.rows_written,0);
 assert.deepEqual(await rows(),original,'unchanged timestamps and provenance preserved');
 const changed=structuredClone(universe);changed[0].company='Changed Biotech';
 before={...totals};assert.equal((await sync(changed)).changed,1);const oneChanged=delta(before);
 const added=[...changed,{...universe[0],cik:'311',ticker:'NEW',company:'New Biotech'}];
 before={...totals};assert.equal((await sync(added)).changed,1);const oneAdded=delta(before);
 const removed=added.slice(1);before={...totals};assert.equal((await sync(removed)).changed,1);const oneRemoved=delta(before);
 assert.equal((await rows()).find(r=>r.cik==='1').active,0,'removal retained, not deleted');
 const stable=await rows();
 for(const [invalid,status] of [[[],400],[removed.slice(0,100),409],
 [universe.map((r,i)=>({...r,cik:String(10000+i)})),409],
 [[...removed,{...removed[0],cik:removed[0].cik.padStart(10,'0')}],400],
 [[{...removed[0],confidence:'0.99'}],400],[[{...removed[0],ticker:'invalid!'}],400]]){
  before={...totals};assert.equal((await syncIssuers(request(invalid),env)).status,status);
  assert.equal(delta(before).rows_written,0);assert.deepEqual(await rows(),stable);
 }
 assert.equal((await syncIssuers(request(removed,'wrong'),env)).status,401);
 assert.equal((await syncIssuers(new Request('https://fixture/edge/sync',{method:'POST',
 headers:{Authorization:'Bearer fixture'},body:'{'}),env)).status,400);
 fail=true;await assert.rejects(()=>sync(changed));fail=false;
 assert.deepEqual(await rows(),stable,'batch failure rolls back removals and updates');
 // Requests may race, but one complete snapshot wins; never a mixed partial state.
 const a=structuredClone(removed),b=structuredClone(removed);
 a[0].company='Snapshot A';a[1].company='Snapshot A';
 b[0].company='Snapshot B';b[1].company='Snapshot B';
 await Promise.all([sync(a),sync(b),sync(b)]);
 const final=await rows();assert.equal(final.find(r=>r.cik===a[0].cik).company,final.find(r=>r.cik===a[1].cik).company);
 const winner=final.find(r=>r.cik===a[0].cik).company==='Snapshot A'?a:b;
 before={...totals};await Promise.all(Array.from({length:8},()=>sync(winner)));
 assert.equal(delta(before).rows_written,0,'concurrent identical retries are write-free');
 assert.equal(final.filter(r=>r.active).length,310,'unchanged issuers stay eligible');
 assert.equal((await db.prepare('SELECT count(*) AS n FROM edge_events').first()).n,0,'no synthetic events/ACKs');
 // Compare the actual production SQL, including its active index write costs.
 const source=execFileSync('git',['show','7197fb228615914b2a575955ecefe0abd671d28b:cloudflare/src/index.js'],{encoding:'utf8'});
 const oldSql=source.match(/const upsert = db.prepare\("([^"]+)"\)/)[1];
 const oldSync=()=>old.batch([old.prepare('UPDATE edge_issuers SET active=0'),...universe.map(r=>
  old.prepare(oldSql).bind(r.cik,r.ticker,r.company,r.confidence,r.source,'2026-10-10'))]);
 await oldSync();const baseline=(await oldSync()).reduce((sum,r)=>{
  sum.rows_read+=r.meta.rows_read;sum.rows_written+=r.meta.rows_written;sum.queries++;return sum;
 },{rows_read:0,rows_written:0,queries:0});
 assert.equal(baseline.rows_written,1240);
 console.log(JSON.stringify({issuers:310,initialization,unchanged100,oneChanged,oneAdded,oneRemoved,baselinePerIdenticalSync:baseline},null,2));
 assert.ok(unchanged100.rows_read/100<=baseline.rows_read*2,'no significant read amplification');
 console.log('issuer sync: integrity, concurrency, failure rollback and real D1 cost checks passed');
} finally {await mf.dispose();}
