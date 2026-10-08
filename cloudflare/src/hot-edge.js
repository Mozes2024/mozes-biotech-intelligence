// Bounded Stage-0 detector. Python remains authoritative for enrichment and scoring.
const SEC = "https://www.sec.gov/cgi-bin/browse-edgar";
const WIRES = [
  ["businesswire", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeGFNXXw=="],
  ["globenewswire", "https://www.globenewswire.com/RssFeed/industry/4573-Biotechnology/feedTitle/GlobeNewswire%20-%20Industry%20News%20on%20Biotechnology"],
];
const INTERVAL = env => Math.max(10, Math.min(300, Number(env.EDGE_INTERVAL_SECONDS) || 15)) * 1000;
const text = value => String(value || "").replace(/<!\[CDATA\[|\]\]>/g, "")
  .replace(/<[^>]*>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<")
  .replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/\s+/g, " ").trim();
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
  return { polarity: positive && negative ? "mixed" : negative ? "negative" : positive ? "positive" : "unknown",
    material: positive || negative };
}

export function parseSecAtom(xml) {
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

function parseWire(xml) {
  return (String(xml).match(/<item>[\s\S]*?<\/item>/gi) || []).slice(0, 30).map(item => ({
    headline: tag(item, "title"), url: tag(item, "link"),
    published_at: tag(item, "pubDate"), summary: tag(item, "description"),
  })).filter(item => item.headline && /^https?:\/\//.test(item.url));
}

async function get(url, env, sec = false) {
  const response = await fetch(url, { headers: sec ? { "User-Agent": env.SEC_USER_AGENT || "" } : {},
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
    body: JSON.stringify({ ref: env.GITHUB_REF || "main", inputs: { priority_only: true,
      hot_ticker: event.ticker, hot_cik: event.cik || "", hot_accession: event.accession || "",
      hot_source_url: event.source_url } }),
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
    const now = new Date().toISOString();
    const health = await this.state.storage.get("health") || { sources: {} };
    health.last_alarm_at = now;
    try {
      for (const form of ["8-K", "6-K"]) {
        const source = `sec_${form}`;
        try { await this.pollSec(form, now); this.success(health, source, now); }
        catch (error) { this.failure(health, source, now); }
      }
      for (const [name, url] of WIRES) {
        try { await this.pollWire(name, url, now); this.success(health, name, now); }
        catch (error) { this.failure(health, name, now); }
      }
      try { await this.flushPending(); this.success(health, "downstream", now); }
      catch { this.failure(health, "downstream", now); }
    } finally {
      await this.state.storage.setAlarm(Date.now() + INTERVAL(this.env));
      health.next_alarm_at = await this.state.storage.getAlarm();
      await this.state.storage.put("health", health);
    }
  }

  success(health, source, now) {
    health.sources[source] = { ...health.sources[source], last_checked_at: now,
      last_success_at: now, consecutive_errors: 0, status: "OK" };
  }
  failure(health, source, now) {
    const old = health.sources[source] || {};
    health.sources[source] = { ...old, last_checked_at: now, last_error_at: now,
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
    let inspected = 0;
    for (const filing of entries) {
      const issuer = await this.issuer(filing.cik);
      if (!issuer) continue;
      const id = "EDGE-" + (await hash(`sec:${filing.accession}`)).slice(0, 24);
      if (await this.env.ALERTS_DB.prepare("SELECT event_id FROM edge_events WHERE event_id=?").bind(id).first()) continue;
      let content = filing.headline;
      let sourceUrl = filing.url;
      if (inspected++ < 3) {
        const index = await get(filing.url, this.env, true);
        const exhibit = index.match(/href="([^"]*(?:ex[-_]?99|exhibit[-_]?99)[^"]*\.htm[l]?)"/i)?.[1];
        if (exhibit) {
          const candidate = new URL(exhibit.replace(/&amp;/g, "&"), filing.url);
          if (candidate.hostname === "www.sec.gov") {
            sourceUrl = candidate.href;
            content = await get(sourceUrl, this.env, true);
          }
        }
      }
      const outcome = fastOutcome(content);
      await this.persist({ event_id: id, source: "sec", source_url: sourceUrl, ticker: issuer.ticker,
        cik: filing.cik, form, accession: filing.accession, headline: filing.headline,
        accepted_at: filing.accepted_at, published_at: null, first_seen_at: now, ...outcome });
    }
  }

  async pollWire(name, url, now) {
    const items = parseWire(await get(url, this.env));
    const issuers = (await this.env.ALERTS_DB.prepare("SELECT * FROM edge_issuers WHERE active=1 AND confidence>=0.85").all()).results || [];
    for (const item of items) {
      const matches = issuers.filter(row => row.company?.length >= 6 &&
        item.headline.toLowerCase().includes(row.company.toLowerCase()));
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
    const inserted = await db.prepare("INSERT OR IGNORE INTO edge_events(event_id,source,source_url,ticker,cik,form,accession,headline,accepted_at,published_at,first_seen_at,polarity,material) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)")
      .bind(event.event_id, event.source, event.source_url, event.ticker, event.cik, event.form, event.accession || null,
        event.headline?.slice(0, 500), event.accepted_at, event.published_at, event.first_seen_at,
        event.polarity, event.material ? 1 : 0).run();
    if (!inserted.meta?.changes) return;
    await this.deliver(event);
  }

  async flushPending() {
    const rows = (await this.env.ALERTS_DB.prepare("SELECT * FROM edge_events WHERE (material=1 AND stage0_sent_at IS NULL) OR enrichment_queued_at IS NULL ORDER BY first_seen_at LIMIT 10").all()).results || [];
    const outcomes = await Promise.allSettled(rows.map(row => this.deliver(row)));
    if (outcomes.some(item => item.status === "rejected")) throw Error("pending downstream delivery");
  }

  async deliver(event) {
    const db = this.env.ALERTS_DB;
    let failed = false;
    if (event.material && !event.stage0_sent_at && this.env.NTFY_URL) {
      try {
        const response = await fetch(this.env.NTFY_URL, { method: "POST",
          headers: { Title: `Stage-0 ${event.ticker} ${event.source}`, Priority: "4", Tags: "biotech,warning" },
          body: `[Stage-0 ${event.event_id}] ${event.ticker} ${event.polarity}: ${event.headline}\n${event.source_url}` });
        if (response.ok) await db.prepare("UPDATE edge_events SET stage0_sent_at=? WHERE event_id=?")
          .bind(new Date().toISOString(), event.event_id).run();
        else failed = true;
      } catch { failed = true; }
    }
    if (!event.enrichment_queued_at) {
      try {
        if (await dispatchEnrichment(this.env, event))
          await db.prepare("UPDATE edge_events SET enrichment_queued_at=? WHERE event_id=?")
            .bind(new Date().toISOString(), event.event_id).run();
        else failed = true;
      } catch { failed = true; }
    }
    if (failed) throw Error("downstream delivery incomplete");
  }
}
