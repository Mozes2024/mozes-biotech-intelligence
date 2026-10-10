import assert from 'node:assert/strict';
import {readFileSync,readdirSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {HotEdge} from './src/hot-edge.js';
import {database,state} from './test-db.mjs';

const base='116d78d9bce29ef94836a47753c656b59c462871';
const original=execFileSync('git',['show',`${base}:cloudflare/src/hot-edge.js`],{encoding:'utf8'});
const current=readFileSync(new URL('./src/hot-edge.js',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const alarm=s=>s.slice(s.indexOf('  async alarm() {'),s.indexOf('\n  success(health,',s.indexOf('  async alarm() {')));
const pruneStart=original.indexOf("      try{\n        if(await this.state.storage.get('candidate_pruned_day')");
const pruneEnd=original.indexOf("      }catch(error){this.failure(health,'candidate_retention',now,error);}",pruneStart)+"      }catch(error){this.failure(health,'candidate_retention',now,error);}".length;
const retained=original.slice(0,pruneStart)+'      // Rollback retains candidate history; do not restore legacy pruning.'+original.slice(pruneEnd);
if(!current.includes('async drainPending('))assert.equal(current,retained,'rollback changes only pruning');
assert.equal(alarm(current),alarm(retained),'alarm execution/rearming changes only by removing pruning');
assert.equal(current.match(/^const INTERVAL = .*$/m)[0],original.match(/^const INTERVAL = .*$/m)[0]);
assert.ok(!current.includes('async runAlarm('),'no Stage A alarm serialization');
assert.ok(!current.includes('source_item_id'),'no Stage B schema dependency');
assert.deepEqual(readdirSync(new URL('./migrations/',import.meta.url)).sort(),['0001_alert_feed.sql','0002_hot_edge.sql','0003_hot_edge_reliability.sql','0004_clinical_candidates.sql']);
assert.equal(readFileSync(new URL('./src/index.js',import.meta.url),'utf8').replace(/\r\n/g,'\n').split('export async function syncIssuers')[0],
 execFileSync('git',['show',`${base}:cloudflare/src/index.js`],{encoding:'utf8'}).split('export async function syncIssuers')[0],'only issuer sync may change in index');
for(const file of ['edge-contract.js','candidate-contract.js','clinical-events.js']){
 assert.equal(readFileSync(new URL('./src/'+file,import.meta.url),'utf8').replace(/\r\n/g,'\n'),execFileSync('git',['show',`${base}:cloudflare/src/${file}`],{encoding:'utf8'}),'other runtime modules unchanged');
}

const realNow=Date.now;let clock=Date.parse('2026-10-10T12:00:00Z');
try{
 Date.now=()=>clock;
 const db=database(),s=state(),edge=new HotEdge(s,{ALERTS_DB:db,EDGE_INTERVAL_SECONDS:'120'});
 db.sqlite.exec(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<3005)
 INSERT INTO edge_candidates(candidate_id,source,source_url,headline,summary,first_seen_at,classification_json,lifecycle,history_json,content_hash)
 SELECT printf('CAND-%024x',i),'sec','https://www.sec.gov/fixture','Retained','History','2026-09-01','{}','COMPLETED','[]','hash' FROM n`);
 db.sqlite.exec(`INSERT INTO edge_events(event_id,source,source_url,ticker,first_seen_at,polarity,material,analysis_status,enrichment_ack_at)
 VALUES('EDGE-aaaaaaaaaaaaaaaaaaaaaaaa','sec','https://www.sec.gov/fixture','OLD','2026-09-01','unknown',0,'complete','2026-09-02')`);
 const snapshot=()=>JSON.stringify({candidates:db.sqlite.prepare('SELECT * FROM edge_candidates ORDER BY candidate_id').all(),events:db.sqlite.prepare('SELECT * FROM edge_events ORDER BY event_id').all()});
 const before=snapshot();
 s.memory.set('candidate_pruned_day','2026-10-09');
 s.memory.set('unrelated_existing_state',{retain:true});
 edge.pollSec=async()=>{};edge.pollWire=async()=>{};edge.flushPending=async()=>{};
 edge.analyzePending=async()=>({failed:0});
 for(const duration of [20000,130000]){
  const start=clock;edge.pollSec=async()=>{clock=start+duration;};
  await edge.alarm();
  assert.equal(await s.storage.getAlarm(),Math.max(start+120000,start+duration+1000),'original start-based rearming retained');
  assert.equal(snapshot(),before,'all 3005 old candidate records and historical event/ACK preserved');
 }
 const restart=new HotEdge(s,{ALERTS_DB:db,EDGE_INTERVAL_SECONDS:'120'});
 const savedAlarm=await s.storage.getAlarm();
 assert.equal(await restart.arm(),savedAlarm,'existing DO alarm/state remains compatible');
 assert.deepEqual(s.memory.get('unrelated_existing_state'),{retain:true});
 const defaults=new HotEdge(state(),{});clock+=100000;
 assert.equal(await defaults.arm(),clock+15000,'original default interval retained');
 const minimum=new HotEdge(state(),{EDGE_INTERVAL_SECONDS:'1'});
 assert.equal(await minimum.arm(),clock+10000,'original minimum interval retained');
 assert.equal(s.memory.get('candidate_pruned_day'),'2026-10-09','existing pruning marker retained without updating');
 const overlapping=new HotEdge(state(),{EDGE_INTERVAL_SECONDS:'120'});
 let polls=0,release;const gate=new Promise(resolve=>{release=resolve});
 overlapping.pollSec=async()=>{polls++;await gate};overlapping.pollWire=async()=>{};
 overlapping.analyzePending=async()=>({failed:0});overlapping.flushPending=async()=>{};
 const alarms=[overlapping.alarm(),overlapping.alarm()];
 await new Promise(resolve=>setImmediate(resolve));assert.equal(polls,4,'original overlapping alarm execution preserved');
 release();await Promise.all(alarms);
 console.log('production baseline: unchanged scheduler, restart compatibility, 3005 retained candidates, events/ACKs and schema 0001–0004 passed');
}finally{Date.now=realNow;}
