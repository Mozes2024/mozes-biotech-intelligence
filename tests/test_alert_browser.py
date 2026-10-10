import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_popup_policy_separates_list_corrections_from_new_urgent_events():
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const context={};vm.createContext(context);vm.runInContext(fs.readFileSync('web/alerts_ui.js','utf8'),context);
let saved=null,shown=[];
const notifier=context.createAlertNotifier({storage:{getItem:()=>saved,setItem:(_,v)=>saved=v},
 show:rows=>shown.push(rows.map(a=>a.change_id)),system:()=>{},now:()=>1000});
const base={verification_state:'primary_source',priority:'P2'};
const atos={...base,change_id:'ATOS',popup_eligible:false,event_family:'corporate_action'};
const routine={...base,change_id:'routine',priority:'P3',popup_eligible:true};
const unverified={...base,change_id:'unverified',priority:'P1',verification_state:'investigation_only',popup_eligible:true};
const clinical={...base,change_id:'clinical',popup_eligible:true,event_family:'clinical_outcome'};
const urgent={...base,change_id:'urgent',priority:'P1',popup_eligible:true};
(async()=>{
 await notifier.observe([],true);
 await notifier.observe([atos,routine,unverified,clinical,urgent]);
 assert.deepStrictEqual(shown,[['clinical','urgent']]);
 assert.ok(JSON.parse(saved).ATOS,'non-popup list events are still remembered');
 await notifier.observe([{...atos,priority:'P1',popup_eligible:true},clinical,urgent]);
 assert.strictEqual(shown.length,1,'reclassification of existing ID cannot notify again');
 const reloaded=context.createAlertNotifier({storage:{getItem:()=>saved,setItem:(_,v)=>saved=v},show:()=>{throw Error('duplicate')},system:()=>{},now:()=>1001});
 await reloaded.observe([atos,clinical,urgent]);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout


def test_browser_notifies_negative_and_unknown_alerts_once_across_tabs_and_reload():
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const context={};vm.createContext(context);vm.runInContext(fs.readFileSync('web/alerts_ui.js','utf8'),context);
let saved=null,shown=[],system=[],queue=Promise.resolve();
const storage={getItem:()=>saved,setItem:(_,v)=>{saved=v}};
const lock=fn=>{const next=queue.then(fn);queue=next.catch(()=>{});return next};
const options={storage,lock,show:rows=>shown.push(rows.map(x=>x.change_id)),system:a=>system.push(a.change_id),now:()=>1000};
const first=context.createAlertNotifier(options),second=context.createAlertNotifier(options);
const old={change_id:'old',polarity:'positive'},negative={change_id:'negative',polarity:'negative',priority:'P1',verification_state:'primary_source',popup_eligible:true},halt={change_id:'halt',polarity:'unknown',priority:'P1',verification_state:'primary_source',popup_eligible:true};
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


def test_in_site_popup_opens_alerts_without_native_permission_and_deduplicates_reload():
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('web/alerts_ui.js','utf8');let saved=null;
const storage={getItem:()=>saved,setItem:(_,value)=>{saved=value}};
const old={change_id:'CHG-old',ticker:'OLD',headline:'Existing',priority:'P2'};
const fresh={change_id:'CHG-new',ticker:'NEW',headline:'New verified event',priority:'P1',verification_state:'primary_source',popup_eligible:true};
function browser(){
 const open={},dismiss={},popup={hidden:true,innerHTML:'',querySelector:key=>key==='[data-open-alerts]'?open:dismiss};
 const context={D:{alerts:[],alert_config:{}},apiMode:false,page:'dash',document:{hidden:false},
  window:{localStorage:storage},navigator:{},$:id=>{assert.equal(id,'alertPopups');return popup},
  esc:value=>value,alertWhyHe:a=>a.headline,render:()=>{},feed:null};
 context.fetch=async()=>({ok:true,json:async()=>context.feed});
 vm.createContext(context);vm.runInContext(source,context);
 return {context,popup,open};
}
function feed(rows,revision){return {schema:1,revision,generated_at:'2026-10-10T08:00:00Z',alerts:rows,alert_config:{}}}
(async()=>{
 const first=browser();first.context.feed=feed([old],'baseline');await first.context.checkForAlerts();
 assert.equal(first.popup.hidden,true);
 first.context.feed=feed([fresh,old],'new');await first.context.checkForAlerts();
 assert.equal(first.popup.hidden,false);assert.match(first.popup.innerHTML,/New verified event/);
 assert.equal('Notification' in first.context.window,false,'in-site popup requires no native permission API');
 first.open.onclick();assert.equal(first.context.page,'alerts');assert.equal(first.popup.hidden,true);
 assert.equal(first.context.D.alerts[0].change_id,'CHG-new','new alert is available to Alerts rendering');
 await first.context.checkForAlerts();assert.equal(first.popup.hidden,true,'same revision does not repeat');
 const reloaded=browser();reloaded.context.feed=feed([fresh,old],'new');await reloaded.context.checkForAlerts();
 reloaded.context.feed=feed([fresh,old],'later-revision');await reloaded.context.checkForAlerts();
 assert.equal(reloaded.popup.hidden,true,'persisted seen IDs prevent duplicate popup after reload');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
