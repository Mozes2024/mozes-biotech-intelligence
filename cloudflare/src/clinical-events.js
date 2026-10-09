import rules from "../../mozes/data/clinical_event_rules.json" with { type: "json" };
export const CLASSIFICATION_POLICY = JSON.stringify(rules);

export function classifyClinical(raw, outcome = { material: false, polarity: "unknown" }) {
  const sample = String(raw || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").slice(0, 4000)
    .replace(/\bno\s+(?:new\s+)?clinical\s+data\b/gi, "no new information");
  const has = (key, value = sample) => new RegExp(rules[key], "i").test(value);
  const futureMatch = new RegExp(rules.future,'i').exec(sample.slice(0,700));
  const confirmed = new RegExp(rules.confirmed,'i').exec(sample.slice(0,220));
  const future = Boolean(futureMatch && (!confirmed || futureMatch.index < confirmed.index));
  const relevant = has("clinical");
  const routine = has("conference") && has("routine") && !has("data") && !has("regulatory");
  const management = has("management", sample.slice(0, 220)) && !has("data", sample.slice(0, 220)) && !has("milestone", sample.slice(0, 220));
  const catalyst = relevant && future && (has("data") || has("regulatory") || has("milestone")) && !routine && !management;
  const actionable = !future && !routine && !management && (Boolean(outcome.material) || has("regulatory_event"));
  const material = actionable || catalyst && (has("important") || has("regulatory")) || relevant&&has('milestone')&&has('important')&&!routine&&!management;
  const kind = routine ? "routine_conference" : management ? "management_update" : catalyst ? "upcoming_catalyst" : actionable ? "confirmed_development" : relevant ? "clinical_update" : "outside_scope";
  return { relevant, material, actionable, catalyst, polarity: future || routine || management ? "unknown" : outcome.polarity,
    kind, suppression_reason: routine ? "routine_conference" : !relevant || management ? "outside_scope" : !material ? "low_materiality" : null,
    method: rules.version };
}
