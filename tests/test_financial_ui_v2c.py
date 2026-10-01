"""Render the browser overlay with explicit host-function fixtures (no network)."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_financial_overlay_handles_missing_and_hostile_source_text_without_scoring():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for browser-overlay unit test')
    script = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const ctx={D:{intelligence_v2c:{regulatory_review_count:2}},
 esc:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
 focusCard:a=>'<article><div class="focus-open">open</div></article>',
 market:a=>'<section>market</section>',system:()=>'<section>system</section>',
 changeDescription:c=>'legacy',simpleSignal:(l,v)=>`<span>${l}: ${v}</span>`};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
assert(ctx.focusCard({}).includes('focus-open'));
assert(ctx.market({}).includes('market'));
const row={recommendation:{status:'RESEARCH_WORTHY'},financial_context:{status:'available',estimated_remaining_months:3.4,period_end:'2026-06-30',filed:'2026-08-10',cash_usd:20e6,investments_usd:null,burn_per_month_usd:2e6,liquidity_basis:'cash_only'},funding_window:{status:'estimate_before_window'},financing_events:[{kind:'registration_only',form:'S-3',source_url:'https://www.sec.gov/test',facts:[{span:{text:'<script>alert(1)</script>'},context:'<img src=x onerror=alert(1)>'}]}]};
const before=JSON.stringify(row);
const drawer=ctx.market(row),card=ctx.focusCard(row);
assert(!drawer.includes('<script>'));assert(!drawer.includes('<img src=x'));
assert(drawer.includes('&lt;script&gt;'));assert(card.includes('3.4'));
assert(!ctx.changeDescription({change_type:'financing_interpreted',new_value:{kind:'registration_only'}}).includes('legacy'));
assert.strictEqual(ctx.changeDescription({change_type:'other'}),'legacy');
assert.strictEqual(JSON.stringify(row),before);
assert(ctx.system().includes('2'));
'''
    source = Path(__file__).resolve().parents[1] / 'web' / 'financial_context_ui.js'
    subprocess.run([node, '-e', script, str(source)], check=True, timeout=15)
