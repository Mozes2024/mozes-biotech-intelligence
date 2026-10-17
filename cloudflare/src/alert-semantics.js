// Same bounded evidence rules as Python. This module performs no I/O.
import rules from '../../mozes/data/alert_evidence_rules.json' with {type:'json'};
const has = (pattern, value) => new RegExp(pattern, 'i').test(value);
const protect = value => value.replace(new RegExp(rules.abbreviations,'gi'),m=>m.replaceAll('.','~'));
const decode = value => value.replace(/&(?:#(x[0-9a-f]+|[0-9]+)|amp|lt|gt|quot|apos|nbsp);/gi,(whole,numeric)=>{
  if(numeric){const n=Number.parseInt(numeric.replace(/^x/i,''),/^x/i.test(numeric)?16:10);return n>0&&n<=0x10ffff?String.fromCodePoint(n):whole;}
  return ({'&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"','&apos;':"'",'&nbsp;':' '})[whole.toLowerCase()]||whole;
});
export function classifyEvidence(text, basis='headline_only', asOf=null) {
  const parsed=asOf?new Date(asOf):null;
  const match=/^(\d{4})-(\d{2})/.exec(parsed&&!Number.isNaN(parsed.valueOf())?parsed.toISOString():''),now=new Date();
  const year=match?Number(match[1]):now.getUTCFullYear(),month=match?Number(match[2]):now.getUTCMonth()+1;
  const months=['january','february','march','april','may','june','july','august','september','october','november','december'];
  const datedHistory=clause=>[...clause.matchAll(new RegExp(rules.dated_reference,'gi'))].some(m=>{
    const y=Number(m[2]),mon=m[1]?months.indexOf(m[1].toLowerCase())+1:12;return y<year||y===year&&mon<month;
  });
  const raw=decode(String(text || '')).replace(/<(?:script|style|nav|footer)\b[^>]*>[\s\S]*?<\/(?:script|style|nav|footer)>/gi,'').replace(/<[^>]*>/g,' ');
  const limit=rules.limits[basis]||4000;
  let sample = raw.slice(0,limit);
  if(raw.length>limit){const protectedLimit=protect(sample),last=Math.max(...['.','!','?'].map(ch=>protectedLimit.lastIndexOf(ch)));sample=last>=0?sample.slice(0,last+1):'';}
  const disclaimer = new RegExp(rules.disclaimer,'i').exec(sample);
  if(disclaimer) sample=sample.slice(0,disclaimer.index);
  const result={polarity:'unknown',material:false,event_family:'routine_administration',event_outcome:'unconfirmed',
    confirmed_outcome:false,needs_review:raw.length>limit&&!sample,uncertainty_status:disclaimer?'forward_disclaimer':raw.length>limit&&!sample?'truncated_source':'unconfirmed',
    evidence_basis:basis,semantic_version:rules.version,method:rules.version,uncalibrated:true};
  const found=[],excluded=[];
  const protectedText=protect(sample);
  const clauses=protectedText.split(/[.!?]+/).flatMap(sentence=>sentence.split(/;|\b(?:but|however|whereas)\b|\band\s+(?=(?:the company|FDA|the current trial)\b)|,\s*(?=(?:FDA|the current|the company)\b)/i).map(clause=>({clause,conditional:/\bif\b/i.test(sentence)})));
  for(const {clause:raw,conditional} of clauses) {
    const clause=raw.replaceAll('~','.');
    const scopeClause=clause.replace(/\bpending\s+news\b/gi,'');
    if(!clause.trim())continue;
    const status=has(rules.subject_scope,clause)?'subject_scope':(has(rules.historical,clause)||datedHistory(clause))&&!has(rules.current,clause)?'historical':
      conditional?'conditional':has(rules.prospective,scopeClause)?'prospective':null;
    if(status){excluded.push(status);continue;}
    if(/\b(?:did|does|do)\s+not\s+fail\b/i.test(clause)){excluded.push('ambiguous_negation');continue;}
    const before=found.length;
    for(const rule of rules.rules){
      const match=new RegExp(rule.pattern,'i').exec(clause);
      if(!match)continue;
      if(has(rules.negation,clause.slice(0,match.index))){excluded.push('explicit_absence');continue;}
      found.push(rule);
    }
    if(/\b(?:no|not|never|without|neither|denied|denies)\b/i.test(clause)&&/FDA|approval|clinical hold|CRL|Complete Response/i.test(clause)&&found.length===before)excluded.push('explicit_absence');
  }
  const fields=['event_family','event_outcome','polarity','confirmed_outcome','needs_review','uncertainty_status'];
  const assign=rule=>fields.forEach(key=>result[key]=rule[key]);
  if(found.length){
    assign(found.reduce((a,b)=>a.priority>=b.priority?a:b));result.material=true;
    const outcomes=new Set(found.map(r=>r.event_outcome));
    if(outcomes.has('primary_endpoint_met')&&outcomes.has('primary_endpoint_failed')){
      const multiple=new Set(sample.match(/\b(?:Drug|Disease)[A-Z]\w*/g)||[]).size>1;
      Object.assign(result,{polarity:'mixed',event_outcome:multiple?'multiple_programs':'conflicting_results',confirmed_outcome:false,
        needs_review:true,uncertainty_status:multiple?'multiple_subjects':'conflicting'});
    }else if(outcomes.has('primary_endpoint_failed')&&outcomes.has('secondary_endpoint_met'))Object.assign(result,{polarity:'mixed',uncertainty_status:'mixed_endpoints'});
    else if(outcomes.has('numerical_trend')&&outcomes.has('inconclusive_results'))result.polarity='mixed';
    else if(found.some(r=>r.polarity==='positive')&&found.some(r=>r.polarity==='negative'))Object.assign(result,{polarity:'mixed',confirmed_outcome:false,needs_review:true,uncertainty_status:'mixed_results'});
    if(outcomes.size===1&&outcomes.has('numerical_trend'))result.polarity='mixed';
    if(excluded.includes('explicit_absence')&&result.polarity==='positive')Object.assign(result,{polarity:'mixed',confirmed_outcome:false,needs_review:true,uncertainty_status:'conflicting'});
    const authorization=found.find(r=>r.event_outcome==='trial_authorization');if(authorization)assign(authorization);
  }else{
    const status=['subject_scope','ambiguous_negation','explicit_absence','historical','conditional','prospective'].find(s=>excluded.includes(s));
    if(status){
      let family=status==='ambiguous_negation'?'clinical_outcome':'regulatory_context';
      let outcome=({subject_scope:'unattributed_reference',explicit_absence:'explicit_absence',historical:'historical_reference'})[status]||'unconfirmed';
      if(['prospective','conditional'].includes(status)){
        family=/FDA|approval|Voucher/i.test(sample)?'conditional_milestone':'clinical_catalyst';
        outcome=/Voucher/i.test(sample)?'potential_prv':family==='conditional_milestone'?'prospective_approval':/results|readout/i.test(sample)?'upcoming_readout':'prospective_result';
      }
      Object.assign(result,{event_family:family,event_outcome:outcome,uncertainty_status:status,needs_review:['subject_scope','ambiguous_negation'].includes(status),material:status==='ambiguous_negation'});
    }else if(/positive\s+(?:Phase|top.?line|results)|(?:Phase\s*[23]|top.?line).{0,60}(?:results|data)|Phase\s*[23]\s+top.?line|pivotal\s+trial\s+results|interim.{0,35}analysis|FDA\s+decision/i.test(sample)){
      Object.assign(result,{event_family:'clinical_outcome',event_outcome:'results_reported',material:true,needs_review:true,uncertainty_status:'unspecified_endpoint'});
    }
  }
  if(/CVR|contingent value right/i.test(sample)&&!result.confirmed_outcome)Object.assign(result,{event_family:'corporate_action',event_outcome:'conditional_cvr',polarity:'unknown',material:true,needs_review:false,uncertainty_status:'conditional'});
  if(/LawFirm|law firm|securities claims|class.action/i.test(sample))Object.assign(result,{event_family:'corporate_action',event_outcome:'legal_context',polarity:'unknown',material:true,confirmed_outcome:false,needs_review:true,uncertainty_status:'background_context'});
  if(/\bone\s+indication\b/i.test(sample)&&/\banother\b/i.test(sample)&&/Complete Response Letter|CRL/i.test(sample))Object.assign(result,{polarity:'mixed',event_outcome:'multiple_programs',confirmed_outcome:false,needs_review:true,uncertainty_status:'multiple_subjects'});
  return result;
}
