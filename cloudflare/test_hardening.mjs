import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { HotEdge, parseWire } from "./src/hot-edge.js";
import { acknowledge, eventTrace } from "./src/edge-contract.js";
import { calculateMetrics } from "./src/edge-metrics.js";

const sql = name => readFileSync(new URL(`./migrations/${name}`, import.meta.url), "utf8");
export function database() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(sql("0002_hot_edge.sql"));
  sqlite.exec(sql("0003_hot_edge_reliability.sql"));
  sqlite.exec(sql("0004_clinical_candidates.sql"));
  sqlite.exec(sql("0005_d1_optimization.sql"));
  const db = { sqlite, prepare(query) {
    let values = [];
    const statement = { bind(...args) { values = args; return statement; },
      async all() { return { results: sqlite.prepare(query).all(...values) }; },
      async first() { return sqlite.prepare(query).get(...values) || null; },
      async run() { const result = sqlite.prepare(query).run(...values); return { meta: { changes: Number(result.changes) } }; } };
    return statement;
  }, async batch(statements) { sqlite.exec("BEGIN"); try { const result = await Promise.all(statements.map(s => s.run())); sqlite.exec("COMMIT"); return result; } catch(e) { sqlite.exec("ROLLBACK"); throw e; } } };
  sqlite.exec("INSERT INTO edge_issuers VALUES('123','ZZZZ','Novel Bio',0.99,'SEC-v2C-equity',1,'2026-10-08')");
  return db;
}
const state = () => { const map = new Map(); let alarm=null; return {storage:{get:async k=>map.get(k),put:async(k,v)=>map.set(k,v),getAlarm:async()=>alarm,setAlarm:async v=>{alarm=v}}}; };
const now = "2026-10-08T08:00:10.000Z";
const later = "2026-10-08T08:16:10.000Z";
const edgeId = i => "EDGE-" + String(i).padStart(24,"0");
const material = i => ({ event_id:edgeId(i), source:"businesswire", source_url:`https://www.businesswire.com/news/${i}`,
 ticker:"ZZZZ",cik:"123",form:"rss",headline:"Novel Bio Phase 2 topline results",accepted_at:null,published_at:"2026-10-08T08:00:00Z",first_seen_at:now,polarity:"unknown",material:true });
const originalFetch = globalThis.fetch;
try {
  // Real SQLite migration: a legacy dispatch is deliberately not completion.
  const legacy=new DatabaseSync(":memory:"); legacy.exec(sql("0002_hot_edge.sql"));
  legacy.exec("INSERT INTO edge_events(event_id,source,source_url,ticker,cik,accession,first_seen_at,polarity,material,enrichment_queued_at) VALUES('old','sec','https://www.sec.gov/doc','ZZZZ','123','acc','2026-10-08','unknown',0,'2026-10-08')");
  legacy.exec(sql("0003_hot_edge_reliability.sql"));
  const old=legacy.prepare("SELECT * FROM edge_events").get();
  assert.equal(old.analysis_status,"pending"); assert.equal(old.enrichment_ack_at,null);
  assert.equal(old.enrichment_dispatch_at,old.enrichment_queued_at);
  legacy.exec(sql('0004_clinical_candidates.sql'));
  legacy.exec("INSERT INTO edge_candidates(candidate_id,source,source_url,headline,summary,first_seen_at,classification_json,lifecycle,history_json,content_hash,completed_at,github_run_id) VALUES('historical','sec','https://www.sec.gov/history','history','','2026-10-08','{}','PUBLISHED','[]','old','2026-10-08','42')");
  const historicalEvent=legacy.prepare('SELECT * FROM edge_events').get();
  const historicalCandidate=legacy.prepare('SELECT * FROM edge_candidates').get();
  legacy.exec(sql('0005_d1_optimization.sql'));
  assert.deepEqual(legacy.prepare('SELECT * FROM edge_events').get(),historicalEvent);
  const preserved=legacy.prepare('SELECT * FROM edge_candidates').get();
  assert.equal(preserved.source_item_id,null);delete preserved.source_item_id;
  assert.deepEqual(preserved,historicalCandidate);

  const db=database(), edge=new HotEdge(state(),{ALERTS_DB:db,GITHUB_TOKEN:"secret",GITHUB_REPO:"test/repo",SEC_USER_AGENT:"Operator test@example.com"});
  const filings=Array.from({length:5},(_,i)=>{
    const accession=`0000000123-26-${String(i+1).padStart(6,"0")}`;
    const base=`https://www.sec.gov/Archives/edgar/data/123/${accession.replaceAll("-","")}/`;
    return {accession,base,url:base+"filing-index.htm"};
  });
  const atom="<feed>"+filings.map(f=>`<entry><title>8-K - Novel Bio (123) (Filer)</title><link href="${f.url}"/><id>accession-number=${f.accession}</id><updated>2026-10-08T08:00:00Z</updated></entry>`).join("")+"</feed>";
  const reads=[];
  globalThis.fetch=async url=>{reads.push(String(url));return new Response(String(url).includes("browse-edgar")?atom:String(url).endsWith("filing-index.htm")?'<tr><td><a href="ex99.htm">Exhibit</a></td><td>EX-99.1</td></tr>':"Novel Bio announces topline Phase 2 results");};
  await edge.pollSec("8-K",now);
  assert.equal(db.sqlite.prepare("SELECT COUNT(*) AS n FROM edge_events WHERE analysis_status='pending'").get().n,5);
  assert.equal((await edge.analyzePending(now)).processed,3);
  assert.equal(db.sqlite.prepare("SELECT COUNT(*) AS n FROM edge_events WHERE analysis_status='pending'").get().n,2);
  assert.equal((await edge.analyzePending(later)).processed,2);
  assert.equal(db.sqlite.prepare("SELECT COUNT(*) AS n FROM edge_events WHERE analysis_status='complete'").get().n,5);
  await edge.pollSec("8-K",later); await edge.analyzePending(later);
  assert.equal(db.sqlite.prepare("SELECT COUNT(*) AS n FROM edge_events").get().n,5);
  assert.equal(reads.filter(x=>x.endsWith("ex99.htm")).length,5);

  // SEC inline-XBRL viewer links resolve to the exact accession's primary document.
  const ixDb=database(), ix=new HotEdge(state(),{ALERTS_DB:ixDb,SEC_USER_AGENT:"test"});
  const ixReads=[];
  globalThis.fetch=async url=>{ixReads.push(String(url));return new Response(String(url).includes("browse-edgar")?atom:String(url).endsWith("filing-index.htm")?`<tr><td>8-K</td><td><a href="/ix?doc=${new URL(String(url)).pathname.replace("filing-index.htm","primary.htm")}">primary</a></td></tr>`:"routine filing");};
  await ix.pollSec("8-K",now);
  assert.deepEqual(await ix.analyzePending(now),{processed:3,failed:0});
  assert.equal(ixReads.filter(url=>url.endsWith("primary.htm")).length,3);
  assert.equal(ixReads.some(url=>url.includes("/ix?")),false);
  globalThis.fetch=async()=>new Response('<tr><td>8-K</td><td><a href="/ix?doc=https://attacker.example/primary.htm">primary</a></td></tr>');
  assert.deepEqual(await ix.analyzePending(later),{processed:0,failed:2});

  // A broken exhibit remains retryable and does not block siblings.
  const brokenDb=database(), broken=new HotEdge(state(),{ALERTS_DB:brokenDb,SEC_USER_AGENT:"test"});
  globalThis.fetch=async url=>new Response(String(url).includes("browse-edgar")?atom:String(url).endsWith("filing-index.htm")?'<a href="ex99.htm">EX-99</a>':"Novel Bio met its primary endpoint", {status:String(url)===filings[0].base+"ex99.htm"?503:200});
  await broken.pollSec("8-K",now);
  brokenDb.sqlite.prepare("UPDATE edge_events SET first_seen_at='2026-10-08T08:00:09Z' WHERE accession=?").run(filings[0].accession);
  assert.deepEqual(await broken.analyzePending(now),{processed:2,failed:1});
  const failure=brokenDb.sqlite.prepare("SELECT * FROM edge_events WHERE analysis_status='failed'").get();
  assert.equal(failure.analysis_attempts,1);assert.equal(failure.last_analysis_error,"HTTP 503");
  globalThis.fetch=async url=>new Response(String(url).endsWith("filing-index.htm")?'<a href="ex99.htm">EX-99</a>':"Novel Bio met its primary endpoint");
  assert.deepEqual(await broken.analyzePending(later),{processed:3,failed:0});
  assert.equal(brokenDb.sqlite.prepare("SELECT COUNT(*) AS n FROM edge_events WHERE analysis_status='complete'").get().n,5);

  // 204 only dispatches. Two distinct events are retained; a cancelled run/no ACK retries.
  const queueDb=database(), queue=new HotEdge(state(),{ALERTS_DB:queueDb,GITHUB_TOKEN:"secret",GITHUB_REPO:"test/repo"});
  const dispatched=[];
  globalThis.fetch=async (_url,options)=>{dispatched.push(JSON.parse(options.body).inputs);return new Response(null,{status:204});};
  await queue.persist(material(1)); await queue.flushPending(now);
  let row=queueDb.sqlite.prepare("SELECT * FROM edge_events WHERE event_id=?").get(edgeId(1));
  assert.equal(row.enrichment_ack_at,null);assert.equal(row.enrichment_attempts,1);assert.ok(row.enrichment_dispatch_at);
  await queue.persist(material(2)); await queue.flushPending(now);
  assert.deepEqual(dispatched.map(x=>x.edge_event_id),[edgeId(1),edgeId(2)]);
  await queue.flushPending(later);
  assert.equal(dispatched.length,4);assert.equal(new Set(dispatched.map(x=>x.edge_event_id)).size,2);
  assert.equal(queueDb.sqlite.prepare("SELECT COUNT(*) AS n FROM edge_events WHERE enrichment_ack_at IS NULL").get().n,2);
  const ackBody={edge_event_id:edgeId(1),status:"completed",change_id:"CHG-"+"a".repeat(24),github_run_id:"42",completed_at:later,alert_ids:[],delivery:[]};
  const ackRequest=body=>new Request("https://edge.example/edge/ack",{method:"POST",headers:{Authorization:"Bearer secret"},body:JSON.stringify(body)});
  const ackEnv={ALERTS_DB:queueDb,EDGE_SYNC_TOKEN:"secret"};
  assert.equal((await acknowledge(ackRequest(ackBody),ackEnv,later)).status,200);
  const ackAt=queueDb.sqlite.prepare("SELECT enrichment_ack_at FROM edge_events WHERE event_id=?").get(edgeId(1)).enrichment_ack_at;
  assert.equal((await acknowledge(ackRequest(ackBody),ackEnv,"2026-10-08T08:17:00Z")).status,200);
  assert.equal(queueDb.sqlite.prepare("SELECT enrichment_ack_at FROM edge_events WHERE event_id=?").get(edgeId(1)).enrichment_ack_at,ackAt);
  assert.equal((await acknowledge(ackRequest({...ackBody,change_id:"CHG-"+"b".repeat(24)}),ackEnv,later)).status,409);
  assert.equal((await acknowledge(new Request("https://edge.example/edge/ack",{method:"POST",body:"{}"}),ackEnv)).status,401);
  await queue.flushPending("2026-10-08T09:00:00Z");
  assert.equal(dispatched.filter(x=>x.edge_event_id===edgeId(1)).length,2);
  assert.equal((await eventTrace(new Request(`https://edge.example/edge/event?id=${edgeId(1)}`,{headers:{Authorization:"Bearer secret"}}),ackEnv)).status,200);
  assert.equal((await eventTrace(new Request(`https://edge.example/edge/event?id=${edgeId(1)}`),ackEnv)).status,401);

  const capDb=database(), cap=new HotEdge(state(),{ALERTS_DB:capDb,GITHUB_TOKEN:"secret",GITHUB_REPO:"test/repo"});
  const capDispatch=[];
  globalThis.fetch=async (_url,options)=>{capDispatch.push(JSON.parse(options.body).inputs.edge_event_id);return new Response(null,{status:204});};
  for(let i=10;i<15;i++)await cap.persist(material(i));
  await cap.flushPending(now);await cap.flushPending(now);
  assert.equal(capDispatch.length,3);
  assert.equal(capDb.sqlite.prepare("SELECT COUNT(*) AS n FROM edge_events WHERE enrichment_dispatch_at IS NULL").get().n,2);
  await acknowledge(ackRequest({...ackBody,edge_event_id:capDispatch[0],completed_at:now}),{...ackEnv,ALERTS_DB:capDb},now);
  await cap.flushPending(now);
  assert.equal(capDispatch.length,4);

  const rss=readFileSync(new URL("../tests/fixtures/edge_wire.xml",import.meta.url),"utf8");
  assert.equal(parseWire(rss).length,2);
  const wireDb=database(), wire=new HotEdge(state(),{ALERTS_DB:wireDb});
  globalThis.fetch=async (_url,options)=>{assert.ok(options.headers["User-Agent"]);assert.match(options.headers.Accept,/rss/);return new Response(rss);};
  await wire.pollWire("businesswire","https://feed.example/rss",now);
  assert.equal(wireDb.sqlite.prepare("SELECT COUNT(*) AS n FROM edge_events").get().n,1);
  assert.equal(wireDb.sqlite.prepare("SELECT polarity FROM edge_events").get().polarity,"unknown");
  assert.throws(()=>parseWire("<html>blocked</html>"));
  const healthState=state(), healthEdge=new HotEdge(healthState,{ALERTS_DB:database(),SEC_USER_AGENT:"test"});
  globalThis.fetch=async url=>String(url).includes("globenewswire")?new Response("blocked",{status:520}):new Response(String(url).includes("browse-edgar")?"<feed></feed>":"<rss><channel></channel></rss>");
  await healthEdge.alarm();
  const health=await healthState.storage.get("health");
  assert.equal(health.sources.globenewswire.status,"FAILED");
  assert.equal(health.sources.businesswire.status,"OK");
  assert.equal(health.sources["sec_8-K"].status,"OK");
  for(const source of Object.values(health.sources))for(const field of ["last_checked_at","last_success_at","last_error_at","last_error","consecutive_errors","status"])assert.ok(field in source,field);
  assert.ok(await healthState.storage.getAlarm());
  const metrics=calculateMetrics([{source:"sec",accepted_at:"2026-10-08T08:00:00Z",first_seen_at:now},
    {source:"sec",accepted_at:"2026-10-08T08:00:00Z",first_seen_at:"2026-10-08T08:00:30Z"}],"2026-10-08T08:01:00Z");
  assert.deepEqual(metrics.windows["1h"].by_source.sec.detection,{n:2,p50:20,p95:29,p99:29.8,mean:20});
  assert.equal(metrics.windows["1h"].metrics.stage0_delivery.mean,null);
  assert.equal(calculateMetrics([material(1)],later).windows["1h"].metrics.detection.p50,null);
} finally {globalThis.fetch=originalFetch;}
console.log("edge SQLite queues, ACK, migration, RSS and metrics: passed");
