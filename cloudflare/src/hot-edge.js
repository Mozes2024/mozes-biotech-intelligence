// Bounded Stage-0 detector. Python remains authoritative for enrichment and scoring.
const SEC = "https://www.sec.gov/cgi-bin/browse-edgar";
import {classifyClinical,CLASSIFICATION_POLICY} from './clinical-events.js';
import {POLICY_VERSION, seconds, digest, Usage, instrumentDatabase, applyIssuerSnapshot,persistSecBatch,persistWireBatch} from './edge-runtime.js';
import {edgeMetrics} from './edge-metrics.js';
import {candidateStats,candidates,candidateAck,investigate} from './candidate-contract.js';
import {acknowledge,eventTrace} from './edge-contract.js';
import {handleAlertFeed} from './alert-feed.js';
const WIRES = [
  ["businesswire", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeGFNXXw=="],
  ["globenewswire", "https://rss.globenewswire.com/RssFeed/industry/4573-Biotechnology/feedTitle/GlobeNewswire%20-%20Industry%20News%20on%20Biotechnology"],
];
const INTERVAL = env => Math.max(60, Math.min(300, Number(env.EDGE_INTERVAL_SECONDS) || 60)) * 1000;
const ACK_TIMEOUT = env => Math.max(300, Math.min(86400, Number(env.EDGE_ACK_TIMEOUT_SECONDS) || 900)) * 1000;
const retryDelay = attempts => Math.min(3600000, 60000 * 2 ** Math.min(6, Math.max(0, attempts - 1)));
const text = value => String(value || "").replace(/<!\[CDATA\[|\]\]>/g, "")
  .replace(/<[^>]*>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<")
  .replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/\s+/g, " ").trim();
const companyKey = value => String(value || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ")
  .replace(/\b(?:inc|incorporated|corp|corporation|ltd|limited|plc|holdings)\b/g, " ")
  .replace(/\s+/g, " ").trim();
const tag = (xml, name) => text(xml.match(new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${name}>`, "i"))?.[1]);
const hash = async value => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value))))
  .map(x => x.toString(16).padStart(2, "0")).join("");

export function fastOutcome(raw) {
  const value = text(raw).slice(0, 4000);
  const holdLifted = /\b(?:lifted|lifts|removed|removes)\b.{0,35}\bclinical hold\b|\bclinical hold\b.{0,35}\b(?:lifted|removed)\b/i.test(value);
  const negative = /\b(?:did not meet|failed to meet|missed)\b.{0,45}\bprimary\s+end\s*-?point\b|\b(?:complete response letter|CRL|futility|not statistically significant)\b/i.test(value) ||
    (!holdLifted && /\bclinical hold\b/i.test(value));
  const positiveText = value.replace(/\bnot statistically significant\b/gi, "");
  const positive = holdLifted || /\b(?:met|meets|achieved)\b.{0,45}\bprimary\s+end\s*-?point\b|\bpositive\s+(?:topline|top-line|results|phase)|\bstatistically significant\b|\b(?:FDA|EMA)\s+approv(?:es|ed|al)\b/i.test(positiveText);
  const materialEvent = /\b(?:topline|top-line)\b.{0,70}\b(?:results|data)\b|\bphase\s*[23]\b.{0,60}\b(?:results|analysis|data)\b|\bpivotal\b.{0,40}\bresults\b|\binterim\b.{0,35}\banalysis\b|\b(?:FDA|PDUFA)\b.{0,50}\b(?:decision|extension|extended|approval|CRL)\b|\b(?:trial|study)\b.{0,35}\b(?:discontinu|terminat)\w*\b|\b(?:merger|acquisition|acquires|to acquire)\b/i.test(value);
  return { polarity: positive && negative ? "mixed" : negative ? "negative" : positive ? "positive" : "unknown",
    material: positive || negative || materialEvent };
}

export function parseSecAtom(xml) {
  if (!/<feed\b/i.test(String(xml))) throw Error("invalid SEC Atom feed");
  const entries = String(xml).match(/<entry>[\s\S]*?<\/entry>/gi) || [];
  return entries.map(entry => {
    const title = tag(entry, "title");
    const match = title.match(/^(8-K|6-K) - (.+?) \((\d+)\) \(Filer\)/);
    const url = entry.match(/<link[^>]+href="([^"]+)/)?.[1];
    const accession = tag(entry, "id").match(/accession-number=([\d-]+)/)?.[1];
    return match && url && accession ? { form: match[1], company: match[2], cik: String(Number(match[3])),
      url, accession, accepted_at: tag(entry, "updated"), headline: title } : null;
  }).filter(Boolean);
}

export function parseWire(xml) {
  if (!/<rss\b/i.test(String(xml))) throw Error("invalid wire RSS feed");
  return (String(xml).match(/<item>[\s\S]*?<\/item>/gi) || []).map(item => ({
    source_item_id: tag(item, "guid") || tag(item, "link"),
    headline: tag(item, "title"), url: tag(item, "link"),
    published_at: tag(item, "pubDate"), summary: tag(item, "description"),
  })).filter(item => item.headline && /^https?:\/\//.test(item.url));
}

async function get(url, env, sec = false) {
  const response = await fetch(url, { headers: { "User-Agent": sec ? env.SEC_USER_AGENT || "" : "MOZES-HotEdge/1.0",
    Accept: sec ? "application/atom+xml, application/xml, text/html" : "application/rss+xml, application/xml" },
    signal: AbortSignal.timeout(8000) });
  if (!response.ok) throw Error(`HTTP ${response.status}`);
  const body = await response.text();
  if (body.length > 1024 * 1024) throw Error("source too large");
  return body;
}

async function dispatchEnrichment(env, event) {
  if (!env.GITHUB_TOKEN) return false;
  const response = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.WORKFLOW_FILE || "lightweight-monitor.yml"}/dispatches`, {
    method: "POST", headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json",
      "User-Agent": "mozes-hot-edge", "Content-Type": "application/json" },
    signal: AbortSignal.timeout(8000),
    body: JSON.stringify({ ref: env.GITHUB_REF || "main", inputs: { priority_only: "true",
      hot_ticker: event.ticker, hot_cik: event.cik || "", hot_accession: event.accession || "",
      hot_source_url: event.source_url, edge_event_id: event.event_id } }),
  });
  return response.status === 204;
}

export class HotEdge {
  constructor(state, env) {
    this.state = state; this.env = env;
    this.usage = new Usage();
    this.db = env.ALERTS_DB ? instrumentDatabase(env.ALERTS_DB, this.usage) : null;
    this.committed = new Map(); this.candidateCache = new Map();
    this.cacheStats = {issuer_hits:0,issuer_misses:0,item_hits:0,item_misses:0,conditional_hits:0};
    this.ready = this.restore();
  }

  async restore() {
    if (!this.state.storage.get) return;
    const saved = await this.state.storage.get('optimization');
    if (!saved) return;
    this.usage.data = saved.usage || this.usage.data;
    const chunks = await Promise.all(Array.from({length:saved.chunk_count || 0},(_,i)=>this.state.storage.get(`dedup:${i}`)));
    this.committed = new Map(chunks.flatMap(chunk=>chunk || []));
    this.savedChunks = chunks.map(chunk=>JSON.stringify(chunk));
    this.sourceState = saved.sources || {};
    this.universeHash = saved.universe_hash;
    this.summaryCache = await this.state.storage.get('summary');
    this.pruneCache();
  }

  pruneCache(now = Date.now()) {
    for (const cache of [this.committed,this.candidateCache]) {
      for (const [key,row] of cache) if (row.expires <= now) cache.delete(key);
      while (cache.size > 12000) cache.delete(cache.keys().next().value);
    }
  }

  async checkpoint() {
    const next=(this.checkpointing || Promise.resolve()).catch(()=>{}).then(()=>this.saveCheckpoint());
    this.checkpointing=next;
    return next;
  }

  async saveCheckpoint() {
    this.pruneCache();
    if (!this.state.storage.put) return;
    // Keep every value under the Durable Object 128 KiB limit. Write changed
    // shards only; a crash can lose optimization state, never D1 event history.
    const entries=[...this.committed], count=Math.ceil(entries.length/200);
    this.savedChunks ||= [];
    for(let i=0;i<count;i++) {
      const chunk=entries.slice(i*200,(i+1)*200), encoded=JSON.stringify(chunk);
      if(encoded!==this.savedChunks[i]) { await this.state.storage.put(`dedup:${i}`,chunk); this.savedChunks[i]=encoded; }
    }
    await this.state.storage.put('optimization', {
      usage:this.usage.data,chunk_count:count,sources:this.sourceState || {},universe_hash:this.universeHash,
    });
  }

  async issuers() {
    await this.ready;
    if (this.issuerCache?.expires > Date.now()) { this.cacheStats.issuer_hits++; return this.issuerCache.rows; }
    if (this.issuerLoading) return this.issuerLoading;
    this.cacheStats.issuer_misses++;
    this.issuerLoading = (async () => {
      const rows = (await this.db.prepare('SELECT * FROM edge_issuers WHERE active=1 AND confidence>=0.85').all()).results || [];
      this.issuerCache = {rows,expires:Date.now()+seconds(this.env,'EDGE_ISSUER_CACHE_SECONDS',900,60,3600)*1000};
      return rows;
    })();
    try { return await this.issuerLoading; } finally { this.issuerLoading = null; }
  }

  async sync(rows, force = false) {
    await this.ready;
    return this.exclusive(async () => {
      const normalized = rows.map(r=>({cik:String(Number(r.cik)),ticker:r.ticker,company:r.company.trim(),confidence:Number(r.confidence),source:r.source.trim()})).sort((a,b)=>a.cik.localeCompare(b.cik));
      const signature = await digest(JSON.stringify(normalized));
      if (!force && signature === this.universeHash) return {ok:true,count:rows.length,unchanged:true,changed:0};
      const result = await applyIssuerSnapshot(this.db,normalized);
      this.universeHash = signature; this.issuerCache = null;
      await this.checkpoint();
      return {ok:true,count:rows.length,changed:result.reduce((n,r)=>n+(r.meta?.changes||0),0),universe_hash:signature};
    });
  }

  exclusive(action) {
    const next=(this.serial || Promise.resolve()).catch(()=>{}).then(action);
    this.serial=next;
    return next;
  }

  async summaries() {
    await this.ready;
    if (this.summaryCache?.expires > Date.now()) return this.summaryCache.value;
    if (this.summaryLoading) return this.summaryLoading;
    this.summaryLoading = (async()=>{
      const generated_at = new Date().toISOString();
      const backlog = await this.db.prepare("SELECT COUNT(*) AS events,SUM(CASE WHEN analysis_status<>'complete' THEN 1 ELSE 0 END) AS analysis_pending,SUM(CASE WHEN material=1 AND enrichment_ack_at IS NULL THEN 1 ELSE 0 END) AS enrichment_pending FROM edge_events").first();
      const value = {generated_at,backlog,clinical_operations:await candidateStats(this.db),metrics:await edgeMetrics(this.db)};
      const mode=this.usage.report(this.env).mode;
      const multiplier=mode==='PROTECTION'?12:mode==='ECO'?3:1;
      this.summaryCache = {value,expires:Date.now()+seconds(this.env,'EDGE_HEALTH_CACHE_SECONDS',300,60)*multiplier*1000};
      if(this.state.storage.put)await this.state.storage.put('summary',this.summaryCache);
      return value;
    })();
    try { return await this.summaryLoading; } finally { this.summaryLoading = null; }
  }

  async arm() {
    const existing = await this.state.storage.getAlarm();
    if (existing === null || existing < Date.now()) await this.state.storage.setAlarm(Date.now() + INTERVAL(this.env));
    return this.state.storage.getAlarm();
  }

  async fetch(request) {
    await this.ready;
    const path = new URL(request.url).pathname;
    const env={...this.env,ALERTS_DB:this.db};
    const handlers={'/alerts':handleAlertFeed,'/edge/ack':acknowledge,'/edge/candidates':candidates,
      '/edge/candidate/ack':candidateAck,'/edge/investigate':investigate,'/edge/event':eventTrace};
    if (path==='/alerts' && request.method==='GET') {
      if(this.alertCache?.expires>Date.now())return this.alertCache.response.clone();
      const loading=this.alertLoading ||= {promise:handleAlertFeed(request,env),generation:this.alertGeneration || 0};
      try {
        const response=await loading.promise;
        if(response.ok && loading.generation===(this.alertGeneration || 0))this.alertCache={response:response.clone(),expires:Date.now()+5000};
        return response.clone();
      } finally {if(this.alertLoading===loading)this.alertLoading=null;}
    }
    if (handlers[path]) {
      const response=await (['/edge/ack','/edge/candidate/ack'].includes(path)
        ? this.exclusive(()=>handlers[path](request,env))
        : handlers[path](request,env));
      if(path==='/alerts' && request.method==='POST' && response.ok) {this.alertCache=null;this.alertLoading=null;this.alertGeneration=(this.alertGeneration || 0)+1;}
      return response;
    }
    if (path === "/rearm") return Response.json({ next_alarm_at: new Date(await this.arm()).toISOString() });
    if (path === '/sync' && request.method === 'POST') {
      const body=await request.json();
      return Response.json(await this.sync(body.issuers,body.force===true));
    }
    if (path === '/summaries') return Response.json(await this.summaries());
    if (path === "/health") {
      const health = await this.state.storage.get("health") || {};
      const source_schedule=Object.fromEntries(Object.entries(this.sourceState || {}).map(([name,s])=>[name,{
        effective_interval_seconds:s.effective_interval_seconds,next_at:s.next_at,last_success_at:s.last_success_at,
        high_water_at:s.high_water_at,gap:s.gap,failures:s.failures,
      }]));
      return Response.json({ ...health, cache:{...this.cacheStats,scope:'current DO instance',issuer_hit_rate:this.cacheStats.issuer_hits/(this.cacheStats.issuer_hits+this.cacheStats.issuer_misses || 1),item_hit_rate:this.cacheStats.item_hits/(this.cacheStats.item_hits+this.cacheStats.item_misses || 1)},source_schedule,
        budget:this.usage.report(this.env),sec_policy_scan:await this.state.storage.get('sec_policy_scan'),next_alarm_at: await this.state.storage.getAlarm() });
    }
    return new Response("Not found", { status: 404 });
  }

  async alarm() {
    await this.ready;
    if (this.running) return this.running;
    this.running = this.exclusive(()=>this.runAlarm());
    try { return await this.running; } finally { this.running = null; }
  }

  async runAlarm() {
    this.sourceFetches=0;
    const started = Date.now();
    const now = new Date().toISOString();
    const health = await this.state.storage.get("health") || { sources: {} };
    health.last_alarm_at = now;
    health.alarm_running_at = now;
    // A watchdog may rearm during I/O; the instance guard keeps one cycle active.
    await this.state.storage.setAlarm(started + INTERVAL(this.env));
    await this.state.storage.put("health", health);
    try {
      const sources = [["sec_8-K", () => this.pollSec("8-K")], ["sec_6-K", () => this.pollSec("6-K")],
        ...WIRES.map(([name, url]) => [name, () => this.pollWire(name, url)])];
      await Promise.all(sources.map(async ([name, poll]) => {
        this.sourceState ||= {};
        const schedule = this.sourceState[name] ||= {};
        const mode = this.usage.report(this.env).mode;
        const sec = name.startsWith('sec_');
        const base = seconds(this.env,sec ? name==='sec_8-K'?'EDGE_SEC_8K_INTERVAL_SECONDS':'EDGE_SEC_6K_INTERVAL_SECONDS' : name === 'businesswire' ? 'EDGE_BUSINESSWIRE_INTERVAL_SECONDS' : 'EDGE_GLOBENEWSWIRE_INTERVAL_SECONDS',sec ? seconds(this.env,'EDGE_SEC_INTERVAL_SECONDS',120) : 180);
        schedule.effective_interval_seconds = base * (mode === 'PROTECTION' ? sec ? 2 : 4 : mode === 'ECO' ? sec ? 1 : 2 : 1);
        if (schedule.next_at > Date.now()) return;
        try { await poll(); this.success(health, name, new Date().toISOString()); schedule.failures=0; schedule.last_success_at=new Date().toISOString(); }
        catch (error) { this.failure(health, name, new Date().toISOString(), error); schedule.failures=(schedule.failures||0)+1; }
        schedule.next_at = Date.now() + Math.max(schedule.effective_interval_seconds*1000,schedule.failures ? retryDelay(schedule.failures) : 0);
      }));
      try {
        const capacity=Math.max(1,Math.min(seconds(this.env,'EDGE_ANALYSIS_BATCH_SIZE',12,1,12),Math.floor((40-this.sourceFetches)/2)));
        const analysis = await this.analyzePending(undefined,capacity);
        if (analysis.failed) this.failure(health, "sec_analysis", new Date().toISOString(), Error(`${analysis.failed} filing analyses retryable`));
        else this.success(health, "sec_analysis", new Date().toISOString());
        if(!analysis.processed && !analysis.failed) {
          try { if(await this.backfillSec())this.success(health,'sec_backfill',new Date().toISOString()); }
          catch(error) { this.failure(health,'sec_backfill',new Date().toISOString(),error); }
        }
      } catch (error) { this.failure(health, "sec_analysis", new Date().toISOString(), error); }
      try { await this.flushPending(); this.success(health, "downstream", now); }
      catch (error) { this.failure(health, "downstream", now, error); }
      // Historical candidate/alert records are retained. Only dedup state expires.
      await this.reconcileGaps(health);
    } finally {
      await this.state.storage.setAlarm(Date.now() + INTERVAL(this.env));
      health.next_alarm_at = await this.state.storage.getAlarm();
      health.alarm_running_at = null;
      health.monitoring_coverage = Object.values(this.sourceState || {}).some(s=>s.gap) ? 'DEGRADED' : 'RECENT_WINDOWS';
      health.budget = this.usage.report(this.env);
      await this.checkpoint();
      await this.state.storage.put("health", health);
    }
  }

  success(health, source, now) {
    health.sources[source] = { ...health.sources[source], last_checked_at: now,
      last_success_at: now, last_error_at: health.sources[source]?.last_error_at || null,
      last_error: null, consecutive_errors: 0, status: "OK" };
  }
  failure(health, source, now, error) {
    const old = health.sources[source] || {};
    health.sources[source] = { ...old, last_checked_at: now, last_error_at: now,
      last_success_at: old.last_success_at || null,
      last_error: /^HTTP \d{3}$/.test(error?.message) ? error.message : "source operation failed; retryable",
      consecutive_errors: (old.consecutive_errors || 0) + 1, status: "FAILED" };
  }

  async issuer(cik) {
    return (await this.issuers()).find(row=>row.cik===cik) || null;
  }

  async pollSec(form, now) {
    if (!this.env.SEC_USER_AGENT) throw Error("SEC identity missing");
    await this.ready;
    const name = `sec_${form}`;
    this.sourceState ||= {};
    const state = this.sourceState[name] ||= {};
    const entries = [], previous = new Set(state.window || []), pageHeads = new Set();
    let continuous = !previous.size, exhausted = false;
    const pages = seconds(this.env,'EDGE_SEC_MAX_PAGES',10,1,20);
    for (let page=0;page<pages;page++) {
      const url = `${SEC}?action=getcurrent&type=${form}&owner=exclude&count=100&start=${page*100}&output=atom`;
      this.sourceFetches=(this.sourceFetches || 0)+1;
      const batch = parseSecAtom(await get(url,this.env,true));
      if (batch.length && pageHeads.has(batch[0].accession)) break;
      if (batch.length) pageHeads.add(batch[0].accession);
      entries.push(...batch);
      continuous ||= batch.some(f=>previous.has(f.accession));
      if (continuous && previous.size) break;
      if (batch.length < 100) { exhausted=true; break; }
    }
    if ((!continuous && previous.size) || (!previous.size && !exhausted))
      this.markGap(state,'SEC window continuity unproven or pagination bound reached');
    now = now || new Date().toISOString();
    const issuers = new Map((await this.issuers()).map(row => [row.cik, row]));
    const pending=[];
    for (const filing of entries) {
      const issuer = issuers.get(filing.cik);
      if (!issuer) continue;
      const id = "EDGE-" + (await hash(`sec:${filing.accession}`)).slice(0, 24);
      const fingerprint = await hash(JSON.stringify([filing.accession,issuer.ticker,issuer.cik,POLICY_VERSION,CLASSIFICATION_POLICY]));
      if (this.cached(id,fingerprint)) continue;
      pending.push({ event_id: id, source: "sec", source_url: filing.url, ticker: issuer.ticker,
        cik: filing.cik, form, accession: filing.accession, headline: filing.headline,
        accepted_at: filing.accepted_at, published_at: null, first_seen_at: now,
        polarity: "unknown", material: false, analysis_status: "pending",fingerprint });
    }
    await persistSecBatch(this.db,pending);
    for(const event of pending)this.remember(event.event_id,event.fingerprint);
    state.window = entries.slice(0,100).map(f=>f.accession);
    state.high_water_at = entries.map(f=>f.accepted_at).sort().at(-1) || state.high_water_at;
    await this.checkpoint();
  }

  async analyzePending(now = new Date().toISOString(), limit = 3) {
    await this.requeueSecPolicy();
    const db = this.db;
    const rows = (await db.prepare("SELECT * FROM edge_events INDEXED BY idx_edge_analysis_pending WHERE source='sec' AND enrichment_ack_at IS NULL AND analysis_status IN ('pending','failed') AND (analysis_retry_at IS NULL OR analysis_retry_at<=?) ORDER BY first_seen_at,event_id LIMIT ?").bind(now,limit).all()).results || [];
    const successes=[],receipts=[];
    // Keep six simultaneous SEC connections at most: three filings, each with
    // sequential index/document requests. Twelve analyses/minute preserves the
    // old 3-per-15-second queue capacity without 48 individual D1 operations.
    for(let offset=0;offset<rows.length;offset+=3)await Promise.all(rows.slice(offset,offset+3).map(async event => {
      const attempts = event.analysis_attempts + 1;
      const retryAt = new Date(Date.parse(now) + retryDelay(attempts)).toISOString();
      try {
        const indexUrl = event.filing_index_url || event.source_url;
        const base = new URL(indexUrl);
        if (base.protocol !== "https:" || base.hostname !== "www.sec.gov" ||
            !base.pathname.startsWith(`/Archives/edgar/data/${Number(event.cik)}/${event.accession.replaceAll("-", "")}/`))
          throw Error("invalid primary SEC URL");
        const index = await get(indexUrl, this.env, true);
        const documentRows = index.match(/<tr\b[^>]*>[\s\S]*?<\/tr>/gi) || [];
        const documentRow = documentRows.find(row => />\s*EX-99[^<]*</i.test(row)) ||
          documentRows.find(row => />\s*(?:8-K|6-K)\s*</i.test(row));
        const href = documentRow?.match(/href="([^"]+)"/i)?.[1] ||
          index.match(/href="([^"]*(?:ex[-_]?99|exhibit[-_]?99)[^"]*\.htm[l]?)"/i)?.[1];
        let candidate = new URL((href || indexUrl).replace(/&amp;/g, "&"), indexUrl);
        // SEC primary documents may be linked through its inline XBRL viewer.
        if (candidate.hostname === "www.sec.gov" && candidate.pathname === "/ix" && candidate.searchParams.has("doc"))
          candidate = new URL(candidate.searchParams.get("doc"), indexUrl);
        if (candidate.protocol !== "https:" || candidate.hostname !== "www.sec.gov" ||
            !candidate.pathname.startsWith(`/Archives/edgar/data/${Number(event.cik)}/${event.accession.replaceAll("-", "")}/`))
          throw Error("invalid primary SEC URL");
        if (!href && /-index\.html?$/i.test(candidate.pathname)) throw Error("primary filing document missing");
        const content = href ? await get(candidate.href, this.env, true) : index;
        const outcome = {...classifyClinical(content,fastOutcome(content)),policy_digest:await this.secPolicyDigest()};
        const record=await this.candidateRecord({...event,source_url:candidate.href,summary:content.slice(0,8000)},outcome,{ticker:event.ticker,cik:event.cik},now);
        const sourceHash=await hash(content);
        successes.push(record);
        receipts.push({event_id:event.event_id,status:'complete',attempts,completed_at:new Date().toISOString(),retry_at:null,source_hash:sourceHash,error:null});
      } catch (error) {
        receipts.push({event_id:event.event_id,status:'failed',attempts,completed_at:null,retry_at:retryAt,source_hash:null,error:/^HTTP \d{3}$/.test(error.message)?error.message:'primary document analysis failed'});
      }
    }));
    const statements=receipts.length?[db.prepare(`WITH incoming AS (SELECT json_extract(value,'$.event_id') AS event_id,value FROM json_each(?))
      UPDATE edge_events SET analysis_status=json_extract(incoming.value,'$.status'),analysis_attempts=json_extract(incoming.value,'$.attempts'),analysis_completed_at=json_extract(incoming.value,'$.completed_at'),analysis_retry_at=json_extract(incoming.value,'$.retry_at'),analysis_source_hash=COALESCE(json_extract(incoming.value,'$.source_hash'),analysis_source_hash),last_analysis_error=json_extract(incoming.value,'$.error'),
      lifecycle=CASE WHEN json_extract(incoming.value,'$.status')='failed' THEN 'SUPPRESSED' ELSE lifecycle END,suppression_reason=CASE WHEN json_extract(incoming.value,'$.status')='failed' THEN 'source_unavailable' ELSE suppression_reason END
      FROM incoming WHERE edge_events.event_id=incoming.event_id`).bind(JSON.stringify(receipts))]:[];
    await persistWireBatch(db,successes,statements);
    return { processed: successes.length, failed: receipts.length-successes.length };
  }

  async secPolicyDigest() {
    return hash(POLICY_VERSION + CLASSIFICATION_POLICY);
  }

  async requeueSecPolicy() {
    // One bounded keyset page per cycle, independent of the publisher's recent
    // window. Persist progress only after D1 succeeds; replay is idempotent.
    if (!this.state.storage.get || !this.state.storage.put) return;
    const policy = await this.secPolicyDigest();
    const saved = await this.state.storage.get('sec_policy_scan');
    const scan = saved?.policy === policy ? saved : {policy,cursor:'',complete:false};
    if (scan.complete) return;
    const rows = (await this.db.prepare("SELECT event_id FROM edge_events WHERE event_id>? ORDER BY event_id LIMIT 100").bind(scan.cursor).all()).results || [];
    if (rows.length) {
      await this.db.prepare(`UPDATE edge_events SET analysis_status='pending',analysis_retry_at=NULL,analysis_attempts=0,last_analysis_error=NULL
        WHERE source='sec' AND event_id IN (SELECT value FROM json_each(?)) AND enrichment_ack_at IS NULL AND analysis_status='complete'
        AND COALESCE(json_extract(classification_json,'$.policy_digest'),'')<>?`)
        .bind(JSON.stringify(rows.map(row=>row.event_id)),policy).run();
    }
    await this.state.storage.put('sec_policy_scan',{policy,cursor:rows.at(-1)?.event_id || scan.cursor,complete:rows.length<100});
  }

  async candidateRecord(event,outcome,issuer,now,tickerHint=null) {
    const reason=!outcome.relevant?outcome.suppression_reason || 'outside_scope':!issuer?'issuer_unresolved':!outcome.material?outcome.suppression_reason || 'low_materiality':null;
    const history=[{state:'DISCOVERED',at:now},...(issuer?[{state:'RESOLVED',at:now}]:[]),{state:'CLASSIFIED',at:now},...(reason?[{state:'SUPPRESSED',at:now,reason}]:[])];
    return {event_id:event.event_id,source:event.source,source_item_id:event.source_item_id || null,source_url:event.source_url,headline:event.headline.slice(0,500),summary:String(event.summary || '').slice(0,8000),published_at:event.published_at || event.accepted_at || null,first_seen_at:event.first_seen_at || now,
      ticker:issuer?.ticker || null,cik:issuer?.cik || null,ticker_hint:tickerHint,classification_json:JSON.stringify(outcome),lifecycle:reason?'SUPPRESSED':'CLASSIFIED',suppression_reason:reason,history_json:JSON.stringify(history),content_hash:await hash(event.headline+' '+(event.summary || '')),
      relevant:outcome.relevant?1:0,actionable:outcome.actionable?1:0,catalyst:outcome.catalyst?1:0,material:outcome.material?1:0,polarity:outcome.polarity};
  }

  async pollWire(name, url, now) {
    await this.ready;
    this.sourceState ||= {};
    const state = this.sourceState[name] ||= {};
    const issuers = await this.issuers();
    const universe = await hash(JSON.stringify(issuers.map(r=>[r.cik,r.ticker,r.company,r.confidence,r.active]).sort()));
    const context = await hash(universe+POLICY_VERSION+CLASSIFICATION_POLICY);
    const headers = {'User-Agent':'MOZES-HotEdge/1.0',Accept:'application/rss+xml, application/xml'};
    if (state.context===context && state.etag) headers['If-None-Match']=state.etag;
    if (state.context===context && state.modified) headers['If-Modified-Since']=state.modified;
    const response = await fetch(url,{headers,signal:AbortSignal.timeout(8000)});
    this.sourceFetches=(this.sourceFetches || 0)+1;
    if (response.status !== 304 && !response.ok) throw Error(`HTTP ${response.status}`);
    if (response.status===304) {
      if(state.context!==context) throw Error('unexpected conditional response');
      this.cacheStats.conditional_hits++;
      return;
    }
    const xml = await response.text();
    if (!xml || xml.length>1024*1024) throw Error('source unavailable or too large');
    const items = parseWire(xml);
    if (items.length>3000) { this.markGap(state,'RSS processing bound exceeded'); throw Error('RSS item bound exceeded'); }
    const previous = new Set(state.window || []);
    const itemIds=await Promise.all(items.map(item=>hash(item.source_item_id)));
    if (previous.size && !itemIds.some(id=>previous.has(id))) this.markGap(state,'RSS window lost overlap; publisher archive reconciliation required');
    now = now || new Date().toISOString();
    const pending=[],seen=new Set();
    const missingIds=itemIds.filter(id=>!this.committed.has(`wire:${name}:${id}`));
    const identityRows=missingIds.length?(await this.db.prepare('SELECT candidate_id,source_item_id FROM edge_candidates WHERE source=? AND source_item_id IN (SELECT value FROM json_each(?))').bind(name,JSON.stringify(missingIds)).all()).results || []:[];
    const identities=new Map(identityRows.map(row=>[row.source_item_id,row.candidate_id]));
    for (const item of items) {
      if(item.url.length>2048)continue;
      const itemKey=`wire:${name}:`+await hash(item.source_item_id);
      if(seen.has(itemKey))continue;seen.add(itemKey);
      const id = this.committed.get(itemKey)?.event_id || identities.get(await hash(item.source_item_id)) || "EDGE-" + (await hash(`wire:${item.url}`)).slice(0, 24);
      const fingerprint = await hash(JSON.stringify([item,context]));
      if (this.cached(itemKey,fingerprint)) continue;
      const headline = ` ${companyKey(item.headline)} `;
      const symbols=[...new Set((item.summary.match(/\b(?:NASDAQ|NYSE)\s*:\s*([A-Z][A-Z0-9]{0,9})\b/gi)||[]).map(s=>s.split(':')[1].trim().toUpperCase()))];
      const lead=` ${companyKey(item.headline+' '+item.summary.slice(0,800))} `;
      const matches = issuers.filter(row => companyKey(row.company).length >= 6 &&
        (symbols.length===1&&symbols[0]===row.ticker&&lead.includes(` ${companyKey(row.company)} `)||symbols.length===0&&headline.startsWith(` ${companyKey(row.company)} `)));
      const issuer = matches.length===1?matches[0]:null;
      const outcome = classifyClinical(item.headline + " " + item.summary,fastOutcome(item.headline+" "+item.summary));
      let sourceUrl;
      try {sourceUrl=new URL(item.url);}catch{continue;}
      const allowed=name==='businesswire'?['businesswire.com','www.businesswire.com']:['www.globenewswire.com','globenewswire.com','rss.globenewswire.com'];
      if(!allowed.includes(sourceUrl.hostname)||sourceUrl.username||sourceUrl.password||sourceUrl.port)continue;
      sourceUrl.protocol='https:';
      const record=await this.candidateRecord({...item,event_id:id,source:name,source_item_id:await hash(item.source_item_id),source_url:sourceUrl.href,first_seen_at:now},outcome,issuer,now,symbols.length===1?symbols[0]:null);
      pending.push({...record,itemKey,fingerprint});
    }
    // One atomic transaction per bounded chunk: no cache completion before both
    // candidate history and event classification are durably committed.
    for(let i=0;i<pending.length;i+=1000) {
      const batch=pending.slice(i,i+1000);
      await persistWireBatch(this.db,batch);
      for(const item of batch)this.remember(item.itemKey,item.fingerprint,item.event_id);
    }
    state.window = itemIds.slice(0,300);
    state.high_water_at = items.map(i=>Date.parse(i.published_at)).filter(Number.isFinite).sort((a,b)=>a-b).at(-1) || state.high_water_at;
    state.context=context;
    if (response.status !== 304) { state.etag=response.headers.get('ETag'); state.modified=response.headers.get('Last-Modified'); }
    await this.checkpoint();
  }

  cached(id,fingerprint) {
    const row=this.committed.get(id);
    if (row?.fingerprint===fingerprint && row.expires>Date.now()) { this.cacheStats.item_hits++; return true; }
    this.cacheStats.item_misses++; return false;
  }
  remember(id,fingerprint,event_id=null) { this.committed.set(id,{fingerprint,event_id,expires:Date.now()+48*3600000}); this.pruneCache(); }
  markGap(state,reason) {
    state.gap ||= {since:state.high_water_at ? new Date(state.high_water_at).toISOString() : new Date(Date.now()-48*3600000).toISOString(),reason,reconciliation_status:'PENDING',cursor:0};
    state.gap.last_detected_at=new Date().toISOString();
  }
  async backfillSec() {
    const entry=Object.entries(this.sourceState || {}).find(([name,s])=>name.startsWith('sec_') && s.gap && !(s.gap.retry_at>Date.now()));
    if(!entry || !this.env.SEC_USER_AGENT) return;
    const [name,state]=entry, gap=state.gap, form=name.slice(4), issuers=await this.issuers();
    const identity=await hash(JSON.stringify(issuers.map(i=>[i.cik,i.ticker]).sort()));
    if(gap.issuer_hash!==identity) {gap.issuer_hash=identity;gap.cursor=0;}
    const since=gap.since.slice(0,10), limit=seconds(this.env,'EDGE_SEC_BACKFILL_ISSUERS',3,1,10);
    try {
      for(let n=0;n<limit && gap.cursor<issuers.length;n++) {
        const issuer=issuers[gap.cursor];
        const root=JSON.parse(await get(`https://data.sec.gov/submissions/CIK${issuer.cik.padStart(10,'0')}.json`,this.env,true));
        const recent=root.filings?.recent;
        if(!Array.isArray(recent?.accessionNumber)) throw Error('invalid SEC submissions');
        const archives=(root.filings?.files || []).filter(f=>f.filingTo>=since);
        // Date-level lower bound is inclusive: acceptance-time differences cannot
        // discard a filing on the boundary day. Old archive metadata is bounded.
        if(archives.length>3) throw Error('SEC historical backfill bound exceeded');
        const batches=[recent];
        for(const file of archives) {
          if(!/^CIK\d{10}-submissions-\d+\.json$/.test(file.name)) throw Error('invalid SEC archive');
          batches.push(JSON.parse(await get(`https://data.sec.gov/submissions/${file.name}`,this.env,true)));
        }
        const pending=[];
        for(const batch of batches) {
          if(!Array.isArray(batch.accessionNumber)) throw Error('invalid SEC archive data');
          for(let i=0;i<batch.accessionNumber.length;i++) {
            if(batch.form?.[i]!==form || batch.filingDate?.[i]<since) continue;
            const accession=batch.accessionNumber[i];
            if(!/^\d{10}-\d{2}-\d{6}$/.test(accession)) throw Error('invalid SEC accession');
            const base=`https://www.sec.gov/Archives/edgar/data/${Number(issuer.cik)}/${accession.replaceAll('-','')}/`;
            const id='EDGE-'+(await hash(`sec:${accession}`)).slice(0,24);
            const fingerprint=await hash(JSON.stringify([accession,issuer.ticker,issuer.cik,POLICY_VERSION,CLASSIFICATION_POLICY]));
            if(this.cached(id,fingerprint)) continue;
            pending.push({event_id:id,source:'sec',source_url:base+accession+'-index.htm',ticker:issuer.ticker,cik:issuer.cik,form,accession,
              headline:`${form} - ${issuer.company}`,accepted_at:batch.acceptanceDateTime?.[i] || null,published_at:null,first_seen_at:new Date().toISOString(),polarity:'unknown',material:false,analysis_status:'pending',fingerprint});
          }
        }
        await persistSecBatch(this.db,pending);
        for(const event of pending)this.remember(event.event_id,event.fingerprint);
        gap.cursor++;
      }
      if(gap.cursor===issuers.length) {state.last_reconciled_at=new Date().toISOString();state.gap=null;}
      return true;
    } catch(error) {
      gap.attempts=(gap.attempts || 0)+1;gap.retry_at=Date.now()+retryDelay(gap.attempts);throw error;
    }
  }
  async reconcileGaps(health) {
    const gaps=Object.values(this.sourceState || {}).filter(s=>s.gap);
    if (!gaps.length) {if(health.sources.reconciliation)health.sources.reconciliation.status='NOT_NEEDED';return;}
    if (!this.env.GITHUB_TOKEN) return;
    const last=await this.state.storage.get('reconciliation_requested_at') || 0;
    if (Date.now()-last<900000) return;
    try {
      const response=await fetch(`https://api.github.com/repos/${this.env.GITHUB_REPO}/actions/workflows/${this.env.WORKFLOW_FILE || 'lightweight-monitor.yml'}/dispatches`,{
        method:'POST',headers:{Authorization:`Bearer ${this.env.GITHUB_TOKEN}`,Accept:'application/vnd.github+json','User-Agent':'mozes-hot-edge','Content-Type':'application/json'},
        body:JSON.stringify({ref:this.env.GITHUB_REF || 'main',inputs:{priority_only:'true'}}),signal:AbortSignal.timeout(8000),
      });
      if (response.status!==204) throw Error('reconciliation dispatch failed');
      await this.state.storage.put('reconciliation_requested_at',Date.now());
      for(const s of gaps) s.gap.reconciliation_status='REQUESTED_UNVERIFIED';
      this.success(health,'reconciliation',new Date().toISOString());
    } catch(e) { this.failure(health,'reconciliation',new Date().toISOString(),e); }
  }

  async classifyEvent(id,outcome){
    const serialized=JSON.stringify(outcome);
    await this.db.prepare("UPDATE edge_events SET relevant=?,actionable=?,catalyst=?,classification_json=?,lifecycle='CLASSIFIED',suppression_reason=?,material=?,polarity=? WHERE event_id=? AND enrichment_ack_at IS NULL AND (classification_json IS NOT ? OR material<>? OR polarity<>?)")
      .bind(outcome.relevant?1:0,outcome.actionable?1:0,outcome.catalyst?1:0,serialized,outcome.suppression_reason,outcome.material?1:0,outcome.polarity,id,serialized,outcome.material?1:0,outcome.polarity).run();
  }

  async persistCandidate(event,scope,issuer,now,tickerHint=null){
    const reason=!scope.relevant?scope.suppression_reason||'outside_scope':!issuer?'issuer_unresolved':!scope.material?scope.suppression_reason||'low_materiality':null;
    const history=[{state:'DISCOVERED',at:now}];
    if(issuer)history.push({state:'RESOLVED',at:now});
    history.push({state:'CLASSIFIED',at:now});
    if(reason)history.push({state:'SUPPRESSED',at:now,reason});
    const digest=await hash(event.headline+' '+(event.summary||''));
    const fingerprint=await hash(JSON.stringify([digest,event.source_url,issuer?.ticker||null,issuer?.cik||null,scope,POLICY_VERSION,CLASSIFICATION_POLICY]));
    const cached=this.candidateCache.get(event.event_id);
    if(cached?.fingerprint===fingerprint && cached.expires>Date.now())return;
    const prior=await this.db.prepare('SELECT * FROM edge_candidates WHERE candidate_id=?').bind(event.event_id).first();
    if(prior){
      const samePolicy=prior.classification_json===JSON.stringify(scope);
      if(samePolicy&&prior.content_hash===digest&&prior.ticker===(issuer?.ticker||null)&&prior.cik===(issuer?.cik||null)&&prior.source_url===event.source_url){
        this.candidateCache.set(event.event_id,{fingerprint,expires:Date.now()+48*3600000});return;
      }
      const reassess=prior.completed_at&&(prior.content_hash!==digest||!samePolicy);
      await this.db.prepare('UPDATE edge_candidates SET headline=?,summary=?,source_url=?,ticker=?,cik=?,classification_json=?,lifecycle=?,suppression_reason=?,history_json=?,content_hash=?,completed_at=NULL,github_run_id=NULL,verification_retry_at=NULL WHERE candidate_id=?')
        .bind(event.headline.slice(0,500),String(event.summary||'').slice(0,8000),event.source_url,issuer?.ticker||null,issuer?.cik||null,JSON.stringify(scope),reason?'SUPPRESSED':'CLASSIFIED',reassess?'classification_uncertain':reason,JSON.stringify([...JSON.parse(prior.history_json),...history].slice(-10)),digest,event.event_id).run();
      this.candidateCache.set(event.event_id,{fingerprint,expires:Date.now()+48*3600000});
      return;
    }
    await this.db.prepare('INSERT OR IGNORE INTO edge_candidates(candidate_id,source,source_url,headline,summary,published_at,first_seen_at,ticker,cik,ticker_hint,classification_json,lifecycle,suppression_reason,history_json,content_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
      .bind(event.event_id,event.source,event.source_url,event.headline.slice(0,500),String(event.summary||'').slice(0,8000),event.published_at||event.accepted_at||null,event.first_seen_at||now,issuer?.ticker||null,issuer?.cik||null,tickerHint,JSON.stringify(scope),reason?'SUPPRESSED':'CLASSIFIED',reason,JSON.stringify(history),digest).run();
    this.candidateCache.set(event.event_id,{fingerprint,expires:Date.now()+48*3600000});
  }

  async persist(event) {
    const db = this.db;
    const status = event.analysis_status || "complete";
    const inserted = await db.prepare("INSERT OR IGNORE INTO edge_events(event_id,source,source_url,ticker,cik,form,accession,headline,accepted_at,published_at,first_seen_at,polarity,material,filing_index_url,analysis_status,analysis_completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
      .bind(event.event_id, event.source, event.source_url, event.ticker, event.cik, event.form, event.accession || null,
        event.headline?.slice(0, 500), event.accepted_at, event.published_at, event.first_seen_at,
        event.polarity, event.material ? 1 : 0, event.source === "sec" ? event.source_url : null,
        status, status === "complete" ? event.first_seen_at : null).run();
    if (!inserted.meta?.changes && event.source!=='sec') {
      await db.prepare("UPDATE edge_events SET source_url=?,headline=?,ticker=?,cik=? WHERE event_id=? AND enrichment_ack_at IS NULL AND (source_url<>? OR headline IS NOT ? OR ticker<>? OR cik IS NOT ?)")
        .bind(event.source_url,event.headline?.slice(0,500),event.ticker,event.cik,event.event_id,event.source_url,event.headline?.slice(0,500),event.ticker,event.cik).run();
    }
    // Delivery is drained separately; discovery cannot be rolled back by dispatch failure.
  }

  async flushPending(now = new Date().toISOString()) {
    await this.ready;
    // Coalesce overlapping drains in this instance; D1 claims also enforce the cap.
    if (this.deliveryRunning) return this.deliveryRunning;
    this.deliveryRunning = this.drainPending(now);
    try { return await this.deliveryRunning; } finally { this.deliveryRunning = null; }
  }

  async drainPending(now) {
    // D1 retains the backlog; cap unacknowledged dispatches so retries cannot flood Actions.
    const active = await this.db.prepare("SELECT COUNT(*) AS n FROM edge_events WHERE material=1 AND enrichment_ack_at IS NULL AND enrichment_retry_at>?").bind(now).first();
    const capacity = Math.max(0, 3 - (active?.n || 0));
    if (!capacity && !this.env.NTFY_URL) return;
    if (!this.deliveryCursorLoaded) {
      this.deliveryCursor = await this.state.storage.get('delivery_cursor');
      this.deliveryCursorLoaded = true;
    }
    const cursor = this.deliveryCursor;
    const validCursor = cursor && typeof cursor.first_seen_at === 'string'
      && Number.isFinite(Date.parse(cursor.first_seen_at)) && /^EDGE-[a-f0-9]{24}$/.test(cursor.event_id);
    let cutoff = validCursor && typeof cursor.round_cutoff === 'string'
      && Number.isFinite(Date.parse(cursor.round_cutoff)) && cursor.round_cutoff <= now ? cursor.round_cutoff : now;
    const limit = this.env.NTFY_URL ? 3 : capacity;
    const pending = this.env.NTFY_URL
      ? "((actionable=1 AND stage0_sent_at IS NULL) OR (enrichment_ack_at IS NULL AND (enrichment_retry_at IS NULL OR enrichment_retry_at<=?)))"
      : "(enrichment_ack_at IS NULL AND (enrichment_retry_at IS NULL OR enrichment_retry_at<=?))";
    const select = `SELECT * FROM edge_events WHERE material=1 AND analysis_status='complete' AND (enrichment_ack_at IS NULL OR (actionable=1 AND stage0_sent_at IS NULL)) AND ${pending} AND first_seen_at<=?`;
    const values = validCursor ? [now, cutoff, cursor.first_seen_at, cursor.event_id] : [now, cutoff];
    const rows = (await this.db.prepare(`${select}${validCursor ? ' AND (first_seen_at,event_id)>(?,?)' : ''} ORDER BY first_seen_at,event_id LIMIT ${limit}`).bind(...values).all()).results || [];
    if (validCursor && rows.length < limit) {
      // Finish the frozen tail before admitting newer arrivals into the next round.
      cutoff = now;
      const head = (await this.db.prepare(`${select} AND (first_seen_at,event_id)<=(?,?) ORDER BY first_seen_at,event_id LIMIT ${limit - rows.length}`).bind(now, cutoff, cursor.first_seen_at, cursor.event_id).all()).results || [];
      rows.push(...head);
    }
    if (rows.length) {
      // Cursor is only a scheduling hint. Advance before I/O so dispatch/source
      // failures cannot pin the queue. D1 deadlines, identities and ACKs stay authoritative.
      const last = rows[rows.length - 1];
      const next = {first_seen_at:last.first_seen_at,event_id:last.event_id,round_cutoff:cutoff};
      await this.state.storage.put('delivery_cursor', next);
      this.deliveryCursor = next;
    } else if (validCursor) {
      // Empty queues need one lookup on subsequent ticks, not repeated wrap scans.
      await this.state.storage.put('delivery_cursor', null);
      this.deliveryCursor = null;
    }
    const outcomes = await Promise.allSettled(rows.map((row, index) => this.deliver(row, now, index < capacity)));
    if (outcomes.some(item => item.status === "rejected")) throw Error("pending downstream delivery");
  }

  async deliver(event, now = new Date().toISOString(), allowDispatch = true) {
    if (!event.material) return;
    allowDispatch = allowDispatch && this.env.EDGE_ENRICHMENT_ENABLED !== "0";
    const db = this.db;
    let failed = false;
    if (event.material && event.actionable!==0 && !event.stage0_sent_at && this.env.NTFY_URL) {
      try {
        const response = await fetch(this.env.NTFY_URL, { method: "POST",
          signal: AbortSignal.timeout(8000),
          headers: { Title: `Stage-0 ${event.ticker} ${event.source}`, Priority: "4", Tags: "biotech,warning" },
          body: `[Stage-0 ${event.event_id}] ${event.ticker} ${event.polarity === "unknown" ? "Material biotech event detected — direction not yet classified" : event.polarity}: ${event.headline}\n${event.source_url}` });
        if (response.ok) await db.prepare("UPDATE edge_events SET stage0_sent_at=? WHERE event_id=?")
          .bind(new Date().toISOString(), event.event_id).run();
        else failed = true;
      } catch { failed = true; }
    }
    if (allowDispatch && !event.enrichment_ack_at && (!event.enrichment_retry_at || event.enrichment_retry_at <= now)) {
      const attempts = (event.enrichment_attempts || 0) + 1;
      const deadline = new Date(Date.parse(now) + ACK_TIMEOUT(this.env)).toISOString();
      const claimed = await db.prepare("UPDATE edge_events SET enrichment_attempts=enrichment_attempts+1,enrichment_retry_at=?,last_enrichment_error=? WHERE event_id=? AND material=1 AND analysis_status='complete' AND enrichment_ack_at IS NULL AND (enrichment_retry_at IS NULL OR enrichment_retry_at<=?) AND (SELECT COUNT(*) FROM edge_events WHERE material=1 AND enrichment_ack_at IS NULL AND enrichment_retry_at>?)<3")
        .bind(deadline, event.enrichment_dispatch_at ? "completion ACK timeout; retrying" : "awaiting completion ACK", event.event_id, now, now).run();
      if (!claimed.meta?.changes) return;
      try {
        if (await dispatchEnrichment(this.env, event))
          await db.prepare("UPDATE edge_events SET enrichment_dispatch_at=COALESCE(enrichment_dispatch_at,?),enrichment_queued_at=? WHERE event_id=? AND enrichment_ack_at IS NULL")
            .bind(now, now, event.event_id).run();
        else throw Error("dispatch rejected");
      } catch {
        await db.prepare("UPDATE edge_events SET last_enrichment_error='dispatch failed; retryable',enrichment_retry_at=? WHERE event_id=? AND enrichment_ack_at IS NULL")
          .bind(new Date(Date.parse(now) + retryDelay(attempts)).toISOString(), event.event_id).run();
        failed = true;
      }
    }
    if (failed) throw Error("downstream delivery incomplete");
  }
}
