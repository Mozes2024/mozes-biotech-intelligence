import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const c = vm.createContext({URL});
vm.runInContext(readFileSync('web/checkpoint_health.js','utf8'), c);
const now = Date.parse('2026-10-27T12:00:00Z');
const recent = '2026-10-27T11:59:00Z';

test('alternate coverage expires without downloading a new snapshot', () => {
  const data = {health:{source_freshness:[
    {source:'official_feed:TYRA',status:'FAILED',covered_by:'official_mirror',last_checked_at:recent,alternate_checked_at:recent},
    {source:'official_feed:X',status:'PARTIAL',covered_by:'wire_issuer',last_checked_at:recent,alternate_checked_at:'2026-10-27T11:29:00Z'},
    {source:'official_feed:old',status:'FAILED',covered_by:'wire_issuer',last_checked_at:'2026-10-27T10:00:00Z'},
  ]}};
  const h = c.MozesCheckpointHealth(data, now);
  assert.equal(h.covered_sources, 1);
  assert.equal(h.uncovered_sources, 1);
  assert.equal(h.current_failed_sources, 1);
  assert.equal(h.current_partial_sources, 1);
  assert.equal(c.MozesCheckpointHealth(data,now+31*60000).covered_sources,0);
});

test('per-source table shows primary failure and successful alternate independently', () => {
  const html = c.MozesSourceHealthDetails([{source:'official_feed:TYRA',status:'FAILED',observation_status:'fetch_error',
    last_error:'HTTP Error 403: Forbidden',last_checked_at:recent,last_success_at:null,consecutive_failures:514,
    source_url:'https://ir.tyra.bio/',alternates:[{kind:'official_mirror',status:'active',checked_at:recent,
      feed:'https://tyrabio.investorroom.com/news-releases?pagetemplate=rss'}]}],now);
  assert.match(html,/HTTP 403/);
  assert.match(html,/הצלחה: לא ידוע/);
  assert.match(html,/כשלים רצופים: 514/);
  assert.match(html,/אתר רשמי חלופי · פעיל · כיסוי חלקי/);
  assert.match(html,/scope="col"/);
  assert.match(html,/scope="row"/);
  assert.match(html,/tyrabio\.investorroom\.com/);
});

test('unconfigured history is separated; missing RSS is not an empty successful scan', () => {
  const html = c.MozesSourceHealthDetails([
    {source:'official_feed:SLS',status:'PARTIAL',observation_status:'no_rss_found',last_checked_at:recent},
    {source:'official_feed:old',status:'FAILED',in_scope:false},
  ],now);
  assert.match(html,/לא נמצא פיד או אינדקס הודעות מאומת/);
  assert.match(html,/<details><summary>תצפיות מחוץ להיקף המוגדר \(1\)/);
  assert.match(html,/אין גיבוי מאומת בתמונה/);
});

test('raw source content and links cannot inject markup or executable URLs', () => {
  const html = c.MozesSourceHealthDetails([{source:'<img src=x onerror=alert(1)>',status:'FAILED',
    last_error:'<script>alert(1)</script>',source_url:'javascript:alert(1)',
    alternates:[{kind:'<script>',status:'fetch_error',error:'<img>',feed:'https://user:secret@example.com'}]}],now);
  assert.doesNotMatch(html,/<script>|<img|href="javascript:|user:secret/);
  assert.match(html,/&lt;script&gt;/);
});

test('future or old alternate timestamps never display active coverage', () => {
  for (const checked_at of ['2026-10-28T00:00:00Z','2026-10-27T10:00:00Z',null]) {
    const html = c.MozesSourceHealthDetails([{source:'IR',status:'FAILED',alternates:[
      {kind:'wire_issuer',status:'active',checked_at}]}],now);
    assert.doesNotMatch(html,/פעיל · כיסוי חלקי/);
    assert.match(html,/בדיקה ישנה או לא ידועה/);
  }
});

test('failed sources lead the table and times use Jerusalem without losing exact timestamps', () => {
  const html = c.MozesSourceHealthDetails([
    {source:'healthy-source',status:'OK',last_checked_at:recent,last_success_at:recent},
    {source:'failed-source',status:'FAILED',last_checked_at:recent},
  ],now);
  assert.ok(html.indexOf('failed-source') < html.indexOf('healthy-source'));
  assert.match(html,/datetime="2026-10-27T11:59:00Z"/);
  assert.match(html,/13:59:00/); // Israel standard time on October 27
  assert.match(html,/המקור הראשי תקין/);
});
