import assert from 'node:assert/strict';
import {database,state} from './test-db.mjs';
import {HotEdge} from './src/hot-edge.js';
import {acknowledge} from './src/edge-contract.js';

const oldFetch=globalThis.fetch;
const now='2026-10-09T09:00:00Z',later='2026-10-09T10:00:00Z';
const request=body=>new Request('https://edge.example/edge/ack',{method:'POST',headers:{Authorization:'Bearer test'},body:JSON.stringify(body)});
const rss=material=>`<rss><channel><item><guid>stable</guid><title>Novel Bio ${material?'Phase 2 results':'appoints CFO'}</title><link>https://www.businesswire.com/news/test</link><description>Novel Bio (NASDAQ: ZZZZ) ${material?'met its primary endpoint.':'routine management announcement.'}</description></item></channel></rss>`;
try {
  const db=database(),env={ALERTS_DB:db,EDGE_SYNC_TOKEN:'test',GITHUB_TOKEN:'fixture',GITHUB_REPO:'test/repo'},s=state(),edge=new HotEdge(s,env);
  globalThis.fetch=async()=>new Response(rss(true));await edge.pollWire('businesswire','https://feed.example',now);
  globalThis.fetch=async()=>new Response(null,{status:204});await edge.flushPending(now);
  const id=db.sqlite.prepare('SELECT event_id FROM edge_events').get().event_id;
  const body={edge_event_id:id,status:'completed',change_id:'CHG-'+'a'.repeat(24),github_run_id:'42',completed_at:later,alert_ids:[],delivery:[]};
  globalThis.fetch=async()=>new Response(rss(false));await edge.pollWire('businesswire','https://feed.example',later);
  assert.equal(db.sqlite.prepare('SELECT material FROM edge_events').get().material,0);
  // Receipt succeeds but history update fails: the same ACK repairs it on retry.
  db.sqlite.exec("CREATE TRIGGER fail_history BEFORE UPDATE ON edge_candidates BEGIN SELECT RAISE(ABORT,'fixture'); END");
  await assert.rejects(()=>acknowledge(request(body),env,later));
  assert.ok(db.sqlite.prepare('SELECT enrichment_ack_at FROM edge_events').get().enrichment_ack_at);
  db.sqlite.exec('DROP TRIGGER fail_history');
  assert.equal((await acknowledge(request(body),env,later)).status,200);
  const completed=db.sqlite.prepare('SELECT * FROM edge_candidates').get();
  assert.equal(completed.lifecycle,'SUPPRESSED');assert.ok(completed.completed_at);
  assert.equal(JSON.parse(completed.history_json).at(-1).state,'SUPERSEDED');
  assert.equal((await acknowledge(request(body),env,later)).status,200);
  assert.equal(db.sqlite.prepare('SELECT history_json FROM edge_candidates').get().history_json,completed.history_json);
  assert.equal((await acknowledge(request({...body,change_id:'CHG-'+'b'.repeat(24)}),env,later)).status,409);
  // A later upgrade cannot reopen an acknowledged event or its candidate.
  globalThis.fetch=async()=>new Response(rss(true));await edge.pollWire('businesswire','https://feed.example',later);
  let dispatches=0;globalThis.fetch=async()=>{dispatches++;return new Response(null,{status:204});};
  await edge.flushPending(later);assert.equal(dispatches,0);
  assert.equal(db.sqlite.prepare('SELECT completed_at FROM edge_candidates').get().completed_at,completed.completed_at);
  // Undispatched suppressed work is still rejected; a failed receipt retries.
  db.sqlite.exec('UPDATE edge_events SET enrichment_ack_at=NULL,enrichment_attempts=0,enrichment_dispatch_at=NULL');
  assert.equal((await acknowledge(request(body),env,later)).status,409);
  db.sqlite.exec("UPDATE edge_events SET enrichment_attempts=1; CREATE TRIGGER fail_ack BEFORE UPDATE OF enrichment_ack_at ON edge_events BEGIN SELECT RAISE(ABORT,'fixture'); END");
  await assert.rejects(()=>acknowledge(request(body),env,later));
  assert.equal(db.sqlite.prepare('SELECT enrichment_ack_at FROM edge_events').get().enrichment_ack_at,null);
  db.sqlite.exec('DROP TRIGGER fail_ack');assert.equal((await acknowledge(request(body),env,later)).status,200);

  const secDb=database(),secState=state(),secEnv={ALERTS_DB:secDb,SEC_USER_AGENT:'fixture'},sec=new HotEdge(secState,secEnv);
  const eventId=n=>'EDGE-'+n.toString(16).padStart(24,'0');
  // Historical filings are absent from today's Atom feed; ACKed history is immutable.
  for(let n=1;n<=105;n++)secDb.sqlite.prepare(`INSERT INTO edge_events(event_id,source,source_url,filing_index_url,ticker,cik,form,accession,headline,first_seen_at,analysis_status,classification_json,material,polarity,enrichment_ack_at,enrichment_change_id)
    VALUES(?,'sec',?,?,'ZZZZ','123','8-K',?,'historical','2026-09-01','complete','{"method":"old"}',0,'unknown',?,?)`)
    .run(eventId(n),`https://www.sec.gov/Archives/edgar/data/123/000000012326${String(n).padStart(6,'0')}/ex99.htm`,`https://www.sec.gov/Archives/edgar/data/123/000000012326${String(n).padStart(6,'0')}/ex99.htm`,`0000000123-26-${String(n).padStart(6,'0')}`,n===1?now:null,n===1?'CHG-'+ 'c'.repeat(24):null);
  const acked=secDb.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(eventId(1));
  secDb.sqlite.prepare('UPDATE edge_events SET stage0_sent_at=? WHERE event_id=?').run(now,eventId(2));
  secDb.sqlite.exec("CREATE TRIGGER fail_requeue BEFORE UPDATE OF analysis_status ON edge_events BEGIN SELECT RAISE(ABORT,'fixture'); END");
  await assert.rejects(()=>sec.requeueSecPolicy());assert.equal(await secState.storage.get('sec_policy_scan'),undefined);
  secDb.sqlite.exec('DROP TRIGGER fail_requeue');await sec.requeueSecPolicy();
  assert.equal(secDb.sqlite.prepare("SELECT COUNT(*) n FROM edge_events WHERE analysis_status='pending'").get().n,99);
  const restarted=new HotEdge(secState,secEnv);await restarted.ready;await restarted.requeueSecPolicy();
  assert.equal(secDb.sqlite.prepare("SELECT COUNT(*) n FROM edge_events WHERE analysis_status='pending'").get().n,104);
  const writes=secDb.counts.writes;await restarted.requeueSecPolicy();assert.equal(secDb.counts.writes,writes);
  globalThis.fetch=async()=>new Response('unavailable',{status:503});
  assert.equal((await restarted.analyzePending(now,1)).failed,1);
  assert.equal((await restarted.analyzePending(now,1)).processed,0);
  globalThis.fetch=async()=>new Response('Novel Bio Phase 2 results met its primary endpoint.');
  // Atomic candidate/classification failure must leave the failed filing retryable.
  secDb.sqlite.exec("CREATE TRIGGER fail_candidate BEFORE INSERT ON edge_candidates BEGIN SELECT RAISE(ABORT,'fixture'); END");
  await assert.rejects(()=>restarted.analyzePending(later,1));
  assert.equal(secDb.sqlite.prepare('SELECT analysis_status FROM edge_events WHERE event_id=?').get(eventId(2)).analysis_status,'failed');
  secDb.sqlite.exec('DROP TRIGGER fail_candidate');await restarted.analyzePending(later,1);
  const classified=secDb.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(eventId(2));
  assert.equal(classified.material,1);assert.equal(classified.analysis_status,'complete');
  assert.equal(classified.stage0_sent_at,now);
  assert.equal(JSON.parse(classified.classification_json).policy_digest,await restarted.secPolicyDigest());
  assert.deepEqual(secDb.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(eventId(1)),acked);
  // Policy changes with the same human version still invalidate the scan.
  restarted.secPolicyDigest=async()=> 'different-rules-digest';await restarted.requeueSecPolicy();
  assert.equal(secDb.sqlite.prepare('SELECT analysis_status FROM edge_events WHERE event_id=?').get(eventId(2)).analysis_status,'pending');
  secDb.sqlite.prepare('UPDATE edge_events SET enrichment_ack_at=? WHERE event_id<>?').run(now,eventId(2));
  globalThis.fetch=async()=>new Response('Novel Bio appoints CFO.');
  await restarted.analyzePending(later,1);
  const downgraded=secDb.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(eventId(2));
  assert.equal(downgraded.material,0);assert.equal(downgraded.analysis_status,'complete');
  assert.equal(JSON.parse(downgraded.classification_json).policy_digest,'different-rules-digest');
  // A lost progress write replays without resetting retry attempts or duplicating history.
  const put=secState.storage.put;
  restarted.secPolicyDigest=async()=> 'next-policy';
  secState.storage.put=async()=>{throw Error('storage unavailable');};
  await assert.rejects(()=>restarted.requeueSecPolicy());
  secDb.sqlite.prepare("UPDATE edge_events SET analysis_status='failed',analysis_attempts=7,analysis_retry_at=? WHERE event_id=?").run(later,eventId(2));
  secState.storage.put=put;await restarted.requeueSecPolicy();
  assert.equal(secDb.sqlite.prepare('SELECT analysis_attempts FROM edge_events WHERE event_id=?').get(eventId(2)).analysis_attempts,7);
  assert.ok(JSON.parse(secDb.sqlite.prepare('SELECT history_json FROM edge_candidates WHERE candidate_id=?').get(eventId(2)).history_json).some(row=>row.state==='SUPPRESSED'));
  let calls=0;globalThis.fetch=async()=>{calls++;return new Response(null,{status:204});};
  await restarted.flushPending(later);assert.equal(calls,0);
  // Actual HTTP ACK waits behind a classification operation, rather than
  // interleaving receipt/history writes with its transaction.
  let release;const gate=new Promise(resolve=>{release=resolve;});
  const operation=edge.exclusive(()=>gate);
  let finished=false;
  db.sqlite.exec("UPDATE edge_events SET first_seen_at='2026-10-07T09:00:00Z'");
  const response=edge.fetch(request({...body,completed_at:'2026-10-08T09:00:00Z'})).then(value=>{finished=true;return value;});
  await new Promise(resolve=>setTimeout(resolve,0));assert.equal(finished,false);
  release();await operation;assert.equal((await response).status,200);
  console.log('cloudflare production readiness: all checks passed');
} finally {globalThis.fetch=oldFetch;}
