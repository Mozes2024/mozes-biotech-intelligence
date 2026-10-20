import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {database,state} from './test-db.mjs';
import {HotEdge,fastOutcome} from './src/hot-edge.js';
import {classifyClinical} from './src/clinical-events.js';

const rapp=JSON.parse(readFileSync(new URL('../tests/fixtures/rapp_20261020.json',import.meta.url)));
const outcome=classifyClinical(rapp.headline+' '+rapp.excerpt,fastOutcome(rapp.headline+' '+rapp.excerpt));
assert.equal(outcome.material,true);assert.equal(outcome.polarity,'positive');
const now=Date.parse('2026-10-20T10:07:38Z');
const stamp=minutes=>new Date(now+minutes*60000).toISOString();
const db=database(),storage=state(),id='EDGE-aadb760b353397b1f5039db7';
for(let i=0;i<32;i++)db.sqlite.prepare(`INSERT INTO edge_events
  (event_id,source,source_url,ticker,first_seen_at,polarity,material,analysis_status,enrichment_attempts)
  VALUES(?,'businesswire','https://www.businesswire.com/blocked','OLD',?,'unknown',1,'complete',200)`)
  .run('EDGE-'+i.toString(16).padStart(24,'0'),new Date(now-12*86400000+i*60000).toISOString());
db.sqlite.prepare(`INSERT INTO edge_events(event_id,source,source_url,ticker,cik,first_seen_at,polarity,material,analysis_status)
  VALUES(?,'sec',?,'RAPP','2012593',?,'positive',1,'complete')`).run(id,rapp.source_url,stamp(1));
const dispatched=[],original=globalThis.fetch;
globalThis.fetch=async(url,options)=>{assert.equal(new URL(url).hostname,'api.github.com');dispatched.push(JSON.parse(options.body).inputs.edge_event_id);return new Response(null,{status:204});};
try{
  const edge=new HotEdge(storage,{ALERTS_DB:db,GITHUB_TOKEN:'offline',GITHUB_REPO:'offline/repo'});
  await edge.flushPending(stamp(0));
  assert.equal(dispatched.length,3);
  await edge.flushPending(stamp(2));
  assert.equal(dispatched.length,3,'first attempts cannot exceed the existing active cap');
  await edge.flushPending(stamp(16));
  assert.equal(dispatched.length,6);
  assert.ok(dispatched.includes(id),'fresh material SEC event gets a first attempt at the next free slot');
  assert.equal(dispatched.filter(x=>x===id).length,1);
  assert.equal(db.sqlite.prepare('SELECT count(*) n FROM edge_events WHERE enrichment_ack_at IS NOT NULL').get().n,0);
  assert.equal(db.sqlite.prepare('SELECT count(*) n FROM edge_events').get().n,33);
  assert.ok(dispatched.slice(3).filter(x=>x!==id).length===2,'old retries retain two slots');

  // One free slot must not rewind the old retry round after a fresh admission.
  const singleDb=database(),singleState=state(),oldId=i=>'EDGE-'+i.toString(16).padStart(24,'0');
  const oldAt=i=>new Date(now-12*86400000+i*60000).toISOString();
  for(let i=0;i<10;i++)singleDb.sqlite.prepare(`INSERT INTO edge_events
    (event_id,source,source_url,ticker,first_seen_at,polarity,material,analysis_status,enrichment_attempts)
    VALUES(?,'businesswire','https://www.businesswire.com/blocked','OLD',?,'unknown',1,'complete',200)`)
    .run(oldId(i),oldAt(i));
  singleDb.sqlite.prepare('UPDATE edge_events SET enrichment_retry_at=? WHERE event_id IN (?,?)')
    .run(stamp(15),oldId(0),oldId(1));
  await singleState.storage.put('delivery_cursor',{first_seen_at:oldAt(5),event_id:oldId(5),round_cutoff:stamp(-1)});
  singleDb.sqlite.prepare(`INSERT INTO edge_events(event_id,source,source_url,ticker,first_seen_at,polarity,material,analysis_status)
    VALUES(?,'sec',?,'RAPP',?,'positive',1,'complete')`).run(id,rapp.source_url,stamp(0));
  const singleEdge=new HotEdge(singleState,{ALERTS_DB:singleDb,GITHUB_TOKEN:'offline',GITHUB_REPO:'offline/repo'});
  await singleEdge.flushPending(stamp(1));
  assert.equal(dispatched.at(-1),id,'the sole free slot admits the fresh event');
  singleDb.sqlite.prepare('UPDATE edge_events SET enrichment_retry_at=NULL WHERE event_id=?').run(oldId(0));
  await singleEdge.flushPending(stamp(2));
  assert.equal(dispatched.at(-1),oldId(6),'the next free slot resumes the retained retry tail, not its oldest head');
}finally{globalThis.fetch=original;}
console.log('RAPP: real clinical classification and bounded fresh-material first-attempt reserve passed');
