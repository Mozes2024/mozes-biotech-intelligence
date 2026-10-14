"""Read-only UI: exact evidence, hostile text, and no decision controls."""
from pathlib import Path
import subprocess


def test_regulatory_review_overlay_is_read_only_and_distinguishes_claims():
    root = Path(__file__).resolve().parents[1]
    source = root / 'web' / 'regulatory_review_ui.js'
    assert source.is_file(), 'Pending regulatory review items currently have no presentation'
    assert 'regulatory_review_ui.js?v=b2-review-v1' in (root / 'web/index.html').read_text(encoding='utf-8')
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const bad='<img src=x onerror=alert(1)>',url='https://www.sec.gov/Archives/edgar/data/123/a/ex99.htm';
const row={review_id:'REG-1',ticker:'AAA',issuer_verified:true,application:{id:'APP-A',program:'Drug A'},
 source_url:url,reason:'outcome_requires_separate_reconciliation',published_at:'2026-09-15',
 retrieved_at:null,observed_at:'2026-09-16',event_timestamp:null,verification_state:'REVIEW_REQUIRED',
 confirmed_outcome:false,evidence:{statement:'FDA approved Drug A. '+bad},evidence_available:true};
const ctx={D:{regulatory_review:{available:true,count:2,items:[row,{...row,review_id:'REG-2',issuer_verified:false,
 application:null,source_url:'javascript:alert(1)'}]}},URL,
 esc:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
 system:()=>'<section>existing system</section>',fetch:()=>{throw Error('Read-only UI made a request')}};
vm.createContext(ctx);const before=JSON.stringify(ctx.D);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
const html=ctx.system();assert(html.includes('existing system'));
assert(html.includes('REG-1')&&html.includes('REG-2')&&html.includes('APP-A')&&html.includes('Drug A'));
assert(html.includes('טענה לא מוכרעת')&&html.includes('אינה תוצאה רגולטורית מאומתת'));
assert(html.includes('outcome_requires_separate_reconciliation')&&html.includes('FDA approved Drug A.'));
assert(html.includes(url)&&html.includes('2026-09-15')&&html.includes('2026-09-16'));
assert(html.includes('לא ידוע')&&html.includes('לא אומתה'));
assert(!html.includes(bad)&&html.includes('&lt;img'));
assert(!html.includes('href="javascript:')&&!html.includes('<button')&&!html.includes('onclick='));
assert.strictEqual(JSON.stringify(ctx.D),before);
ctx.D={};assert(ctx.system().includes('לא זמין'));
ctx.D={regulatory_review:{available:true,count:0,items:[]}};assert(ctx.system().includes('אין פריטים'));
'''
    subprocess.run(['node', '-e', script, str(source)], check=True, timeout=15)
