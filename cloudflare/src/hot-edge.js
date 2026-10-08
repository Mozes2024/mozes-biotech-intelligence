// Bounded Stage-0 detector. Python remains authoritative for enrichment and scoring.
const SEC = "https://www.sec.gov/cgi-bin/browse-edgar";
const WIRES = [
  ["businesswire", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeGFNXXw=="],
  ["globenewswire", "https://rss.globenewswire.com/RssFeed/industry/4573-Biotechnology/feedTitle/GlobeNewswire%20-%20Industry%20News%20on%20Biotechnology"],
];
const INTERVAL = env => Math.max(10, Math.min(300, Number(env.EDGE_INTERVAL_SECONDS) || 15)) * 1000;
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
  return (String(xml).match(/<item>[\s\S]*?<\/item>/gi) || []).slice(0, 30).map(item => ({
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
  constructor(state, env) { this.state = state; this.env = env; }

  async arm() {
    const existing = await this.state.storage.getAlarm();
    if (existing === null || existing < Date.now()) await this.state.storage.setAlarm(Date.now() + INTERVAL(this.env));
    return this.state.storage.getAlarm();
  }

  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === "/rearm") return Response.json({ next_alarm_at: new Date(await this.arm()).toISOString() });
    if (path === "/health") {
      const health = await this.state.storage.get("health") || {};
      return Response.json({ ...health, next_alarm_at: await this.state.storage.getAlarm() });
    }
    return new Response("Not found", { status: 404 });
  }

  async alarm() {
    const started = Date.now();
    const now = new Date().toISOString();
    const health = await this.state.storage.get("health") || { sources: {} };
    health.last_alarm_at = now;
    health.alarm_running_at = now;
    await this.state.storage.setAlarm(started + INTERVAL(this.env));
    await this.state.storage.put("health", health);
    try {
      const sources = [["sec_8-K", () => this.pollSec("8-K")], ["sec_6-K", () => this.pollSec("6-K")],
        ...WIRES.map(([name, url]) => [name, () => this.pollWire(name, url)])];
      await Promise.all(sources.map(async ([name, poll]) => {
        try { await poll(); this.success(health, name, new Date().toISOString()); }
        catch (error) { this.failure(health, name, new Date().toISOString(), error); }
      }));
      try {
        const analysis = await this.analyzePending();
        if (analysis.failed) this.failure(health, "sec_analysis", new Date().toISOString(), Error(`${analysis.failed} filing analyses retryable`));
        else this.success(health, "sec_analysis", new Date().toISOString());
      } catch (error) { this.failure(health, "sec_analysis", new Date().toISOString(), error); }
      try { await this.flushPending(); this.success(health, "downstream", now); }
      catch (error) { this.failure(health, "downstream", now, error); }
    } finally {
      await this.state.storage.setAlarm(Math.max(started + INTERVAL(this.env), Date.now() + 1000));
      health.next_alarm_at = await this.state.storage.getAlarm();
      health.alarm_running_at = null;
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
    return this.env.ALERTS_DB.prepare("SELECT * FROM edge_issuers WHERE cik=? AND active=1 AND confidence>=0.85")
      .bind(cik).first();
  }

  async pollSec(form, now) {
    if (!this.env.SEC_USER_AGENT) throw Error("SEC identity missing");
    const url = `${SEC}?action=getcurrent&type=${form}&owner=exclude&count=100&output=atom`;
    const entries = parseSecAtom(await get(url, this.env, true));
    now = now || new Date().toISOString();
    const issuers = new Map(((await this.env.ALERTS_DB.prepare("SELECT * FROM edge_issuers WHERE active=1 AND confidence>=0.85").all()).results || []).map(row => [row.cik, row]));
    for (const filing of entries) {
      const issuer = issuers.get(filing.cik);
      if (!issuer) continue;
      const id = "EDGE-" + (await hash(`sec:${filing.accession}`)).slice(0, 24);
      await this.persist({ event_id: id, source: "sec", source_url: filing.url, ticker: issuer.ticker,
        cik: filing.cik, form, accession: filing.accession, headline: filing.headline,
        accepted_at: filing.accepted_at, published_at: null, first_seen_at: now,
        polarity: "unknown", material: false, analysis_status: "pending" });
    }
  }

  async analyzePending(now = new Date().toISOString()) {
    const db = this.env.ALERTS_DB;
    const rows = (await db.prepare("SELECT * FROM edge_events WHERE source='sec' AND analysis_status IN ('pending','failed') AND (analysis_retry_at IS NULL OR analysis_retry_at<=?) ORDER BY first_seen_at,event_id LIMIT 3").bind(now).all()).results || [];
    const outcomes = await Promise.all(rows.map(async event => {
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
        const candidate = new URL((href || indexUrl).replace(/&amp;/g, "&"), indexUrl);
        if (candidate.protocol !== "https:" || candidate.hostname !== "www.sec.gov" ||
            !candidate.pathname.startsWith(`/Archives/edgar/data/${Number(event.cik)}/${event.accession.replaceAll("-", "")}/`))
          throw Error("invalid primary SEC URL");
        if (!href && /-index\.html?$/i.test(candidate.pathname)) throw Error("primary filing document missing");
        const content = href ? await get(candidate.href, this.env, true) : index;
        const outcome = fastOutcome(content);
        await db.prepare("UPDATE edge_events SET analysis_status='complete',analysis_attempts=?,last_analysis_error=NULL,analysis_completed_at=?,analysis_retry_at=NULL,analysis_source_hash=?,source_url=?,polarity=?,material=? WHERE event_id=?")
          .bind(attempts, new Date().toISOString(), await hash(content), candidate.href, outcome.polarity, outcome.material ? 1 : 0, event.event_id).run();
        return true;
      } catch (error) {
        await db.prepare("UPDATE edge_events SET analysis_status='failed',analysis_attempts=?,last_analysis_error=?,analysis_retry_at=? WHERE event_id=?")
          .bind(attempts, /^HTTP \d{3}$/.test(error.message) ? error.message : "primary document analysis failed", retryAt, event.event_id).run();
        return false;
      }
    }));
    return { processed: outcomes.filter(Boolean).length, failed: outcomes.filter(x => !x).length };
  }

  async pollWire(name, url, now) {
    const items = parseWire(await get(url, this.env));
    now = now || new Date().toISOString();
    const issuers = (await this.env.ALERTS_DB.prepare("SELECT * FROM edge_issuers WHERE active=1 AND confidence>=0.85").all()).results || [];
    for (const item of items) {
      const headline = ` ${companyKey(item.headline)} `;
      const matches = issuers.filter(row => companyKey(row.company).length >= 6 &&
        headline.includes(` ${companyKey(row.company)} `));
      if (matches.length !== 1) continue;
      const issuer = matches[0];
      const outcome = fastOutcome(item.headline + " " + item.summary);
      if (!outcome.material) continue;
      const id = "EDGE-" + (await hash(`wire:${item.url}`)).slice(0, 24);
      await this.persist({ event_id: id, source: name, source_url: item.url, ticker: issuer.ticker,
        cik: issuer.cik, form: "rss", headline: item.headline, accepted_at: null,
        published_at: item.published_at, first_seen_at: now, ...outcome });
    }
  }

  async persist(event) {
    const db = this.env.ALERTS_DB;
    const status = event.analysis_status || "complete";
    const inserted = await db.prepare("INSERT OR IGNORE INTO edge_events(event_id,source,source_url,ticker,cik,form,accession,headline,accepted_at,published_at,first_seen_at,polarity,material,filing_index_url,analysis_status,analysis_completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
      .bind(event.event_id, event.source, event.source_url, event.ticker, event.cik, event.form, event.accession || null,
        event.headline?.slice(0, 500), event.accepted_at, event.published_at, event.first_seen_at,
        event.polarity, event.material ? 1 : 0, event.source === "sec" ? event.source_url : null,
        status, status === "complete" ? event.first_seen_at : null).run();
    if (!inserted.meta?.changes) return;
    // Delivery is drained separately; discovery cannot be rolled back by dispatch failure.
  }

  async flushPending(now = new Date().toISOString()) {
    // D1 retains the backlog; cap unacknowledged dispatches so retries cannot flood Actions.
    const active = await this.env.ALERTS_DB.prepare("SELECT COUNT(*) AS n FROM edge_events WHERE material=1 AND enrichment_ack_at IS NULL AND enrichment_retry_at>?").bind(now).first();
    const capacity = Math.max(0, 3 - (active?.n || 0));
    const pending = this.env.NTFY_URL
      ? "(stage0_sent_at IS NULL OR (enrichment_ack_at IS NULL AND (enrichment_retry_at IS NULL OR enrichment_retry_at<=?)))"
      : "(enrichment_ack_at IS NULL AND (enrichment_retry_at IS NULL OR enrichment_retry_at<=?))";
    const rows = (await this.env.ALERTS_DB.prepare(`SELECT * FROM edge_events WHERE material=1 AND analysis_status='complete' AND ${pending} ORDER BY first_seen_at,event_id LIMIT 3`).bind(now).all()).results || [];
    const outcomes = await Promise.allSettled(rows.map((row, index) => this.deliver(row, now, index < capacity)));
    if (outcomes.some(item => item.status === "rejected")) throw Error("pending downstream delivery");
  }

  async deliver(event, now = new Date().toISOString(), allowDispatch = true) {
    if (!event.material) return;
    allowDispatch = allowDispatch && this.env.EDGE_ENRICHMENT_ENABLED !== "0";
    const db = this.env.ALERTS_DB;
    let failed = false;
    if (event.material && !event.stage0_sent_at && this.env.NTFY_URL) {
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
      const claimed = await db.prepare("UPDATE edge_events SET enrichment_attempts=enrichment_attempts+1,enrichment_retry_at=?,last_enrichment_error=? WHERE event_id=? AND enrichment_ack_at IS NULL AND (enrichment_retry_at IS NULL OR enrichment_retry_at<=?)")
        .bind(deadline, event.enrichment_dispatch_at ? "completion ACK timeout; retrying" : "awaiting completion ACK", event.event_id, now).run();
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
