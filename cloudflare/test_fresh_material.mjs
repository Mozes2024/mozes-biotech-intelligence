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
}finally{globalThis.fetch=original;}
console.log('RAPP: real clinical classification and bounded fresh-material first-attempt reserve passed');
