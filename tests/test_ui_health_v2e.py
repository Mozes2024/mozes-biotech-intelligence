import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_overlay_health_uses_amber_only_for_bounded_enrichment():
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync('web/live_intelligence_ui.js', 'utf8');
function render(red, amber) {
  const context = {D: {live: [], changes: [], generated_at: '2026-10-02T00:00:00Z',
    validation: {}, coverage: {}, health: {sec_monitoring_enabled: true,
      primary_catalyst_monitoring: {healthy: !red.length, label_he: 'ניטור אירועי הליבה תקין'},
      severity: {red, amber, info: []}}}, HE: {type: {}},
    decisionStatus: () => ({rank: 0}), focusCard: () => '', market: () => '',
    esc: x => String(x ?? ''), pageTitle: () => '', changedCompact: () => ''};
  vm.createContext(context); vm.runInContext(source, context);
  return context.dashboard();
}
const amber = render([], [{module: 'prices', status: 'INCOMPLETE'}]);
assert(amber.includes('המערכת פעילה. 1 שכבות העשרה טרם השלימו ריצה מלאה.'));
assert(amber.includes('amber-note') && !amber.includes('red-note'));
assert(amber.includes('פרטים טכניים'));
const red = render([{module: 'refresh', status: 'FAILED'}], []);
assert(red.includes('red-note'));
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
