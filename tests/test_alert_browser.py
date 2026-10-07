import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_browser_notifies_negative_and_unknown_alerts_once_across_tabs_and_reload():
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const context={};vm.createContext(context);vm.runInContext(fs.readFileSync('web/alerts_ui.js','utf8'),context);
let saved=null,shown=[],system=[],queue=Promise.resolve();
const storage={getItem:()=>saved,setItem:(_,v)=>{saved=v}};
const lock=fn=>{const next=queue.then(fn);queue=next.catch(()=>{});return next};
const options={storage,lock,show:rows=>shown.push(rows.map(x=>x.change_id)),system:a=>system.push(a.change_id),now:()=>1000};
const first=context.createAlertNotifier(options),second=context.createAlertNotifier(options);
const old={change_id:'old',polarity:'positive'},negative={change_id:'negative',polarity:'negative'},halt={change_id:'halt',polarity:'unknown'};
(async()=>{
 await first.observe([old],true);await second.observe([old],true);
 assert.strictEqual(shown.length,0);
 await Promise.all([first.observe([negative,halt,old]),second.observe([negative,halt,old])]);
 assert.strictEqual(shown.length,1);assert.deepStrictEqual(system,['negative','halt']);
 const reloaded=context.createAlertNotifier(options);await reloaded.observe([negative,halt,old]);
 assert.strictEqual(shown.length,1);
 const denied=context.createAlertNotifier({...options,storage:{getItem(){throw Error('blocked')},setItem(){throw Error('blocked')}}});
 await denied.observe([old],true);await denied.observe([negative,old]);await denied.observe([negative,old]);
 assert.strictEqual(shown.length,2); // storage unavailable still suppresses repeated notifications in this tab
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout


def test_alert_poll_uses_cloudflare_and_falls_back_without_regressing_the_feed():
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');let rendered=0,fetched=[];
const storage={getItem:()=>null,setItem:()=>{}};
const context={D:{alerts:[],alert_config:{feed_url:'https://clock.example/alerts'}},apiMode:false,
 document:{hidden:false},window:{localStorage:storage},navigator:{},render:()=>rendered++,
 showAlertPopup:()=>{},showSystemAlert:()=>{},
 fetch:async url=>{fetched.push(url);return {ok:true,json:async()=>({schema:1,revision:'a',generated_at:'2026-10-07T18:00:00Z',
  alerts:[{change_id:'CHG-1',priority:'P1',polarity:'negative'}],alert_config:{feed_url:'https://clock.example/alerts'}})}}};
vm.createContext(context);vm.runInContext(fs.readFileSync('web/alerts_ui.js','utf8'),context);
(async()=>{
 await context.checkForAlerts();assert.strictEqual(context.D.alerts[0].change_id,'CHG-1');
 assert.deepStrictEqual(fetched,['https://clock.example/alerts']);assert.strictEqual(rendered,1);
 context.fetch=async url=>{fetched.push(url);if(url.startsWith('https'))throw Error('offline');
  return {ok:true,json:async()=>({schema:1,revision:'older',generated_at:'2026-10-07T17:00:00Z',alerts:[],alert_config:{}})}};
 await context.checkForAlerts();assert.strictEqual(context.D.alerts.length,1);assert.strictEqual(rendered,1);
 context.fetch=async url=>{fetched.push(url);if(url.startsWith('https'))throw Error('offline');
  return {ok:true,json:async()=>({schema:1,revision:'newer',generated_at:'2026-10-07T19:00:00Z',alerts:[],alert_config:{}})}};
 await context.checkForAlerts();assert.strictEqual(context.D.alerts.length,0);
 await context.checkForAlerts();assert.strictEqual(fetched[5],'https://clock.example/alerts');
 context.document.hidden=true;await context.checkForAlerts();assert.strictEqual(fetched.length,7);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
