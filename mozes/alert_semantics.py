"""Bounded evidence semantics. Rules are shared with the Worker; no state writes."""
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

RULES = json.loads((Path(__file__).parent / 'data/alert_evidence_rules.json').read_text())


def protect_abbreviations(text):
    return re.sub(RULES['abbreviations'], lambda m: m[0].replace('.', '~'), text, flags=re.I)


def classify_evidence(text, basis='headline_only', *, as_of=None):
    reference = datetime.now(timezone.utc)
    if as_of:
        try:
            try:
                value = datetime.fromisoformat(str(as_of).replace('Z', '+00:00'))
            except ValueError:
                value = parsedate_to_datetime(str(as_of))
            reference = (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except (TypeError, ValueError, AttributeError):
            pass
    year, month = reference.year, reference.month
    months = ['january','february','march','april','may','june','july','august','september','october','november','december']
    def dated_history(clause):
        for match in re.finditer(RULES['dated_reference'], clause, re.I):
            period = (int(match[2]), months.index(match[1].lower()) + 1 if match[1] else 12)
            if period < (year, month):
                return True
        return False
    raw = re.sub(r'<(?:script|style|nav|footer)\b[^>]*>.*?</(?:script|style|nav|footer)>', '', unescape(text or ''), flags=re.I | re.S)
    raw = re.sub(r'<[^>]*>', ' ', raw)
    limit = RULES['limits'].get(basis, 4000)
    sample = raw[:limit]
    if len(raw) > limit:
        # Never complete a claim using a prefix whose condition may lie beyond the limit.
        protected_limit = protect_abbreviations(sample)
        last = max(protected_limit.rfind(ch) for ch in '.!?')
        sample = sample[:last + 1] if last >= 0 else ''
    disclaimer = re.search(RULES['disclaimer'], sample, re.I)
    if disclaimer:
        sample = sample[:disclaimer.start()]
    result = dict(polarity='unknown', material=False, event_family='routine_administration',
                  event_outcome='unconfirmed', confirmed_outcome=False, needs_review=len(raw) > limit and not sample,
                  uncertainty_status='forward_disclaimer' if disclaimer else 'truncated_source' if len(raw) > limit and not sample else 'unconfirmed',
                  evidence_basis=basis, semantic_version=RULES['version'],
                  method=RULES['version'], uncalibrated=True)
    found, excluded = [], []
    # Protect abbreviations so a long initial No keeps scope through U.S. FDA.
    protected = protect_abbreviations(sample)
    clauses = []
    for sentence in re.split(r'[.!?]+', protected):
        conditional = bool(re.search(r'\bif\b', sentence, re.I))
        for clause in re.split(r';|\b(?:but|however|whereas)\b|\band\s+(?=(?:the company|FDA|the current trial)\b)|,\s*(?=(?:FDA|the current|the company)\b)', sentence, flags=re.I):
            clauses.append((clause, conditional))
    for raw, conditional in clauses:
        clause = raw.replace('~', '.')
        scope_clause = re.sub(r'\bpending\s+news\b', '', clause, flags=re.I)
        if not clause.strip():
            continue
        status = ('subject_scope' if re.search(RULES['subject_scope'], clause, re.I) else
                  'historical' if (re.search(RULES['historical'], clause, re.I) or dated_history(clause)) and not re.search(RULES['current'], clause, re.I) else
                  'conditional' if conditional else
                  'prospective' if re.search(RULES['prospective'], scope_clause, re.I) else None)
        if status:
            excluded.append(status)
            continue
        if re.search(r'\b(?:did|does|do)\s+not\s+fail\b', clause, re.I):
            excluded.append('ambiguous_negation')
            continue
        before = len(found)
        for rule in RULES['rules']:
            match = re.search(rule['pattern'], clause, re.I)
            if not match:
                continue
            if re.search(RULES['negation'], clause[:match.start()], re.I):
                excluded.append('explicit_absence')
                continue
            found.append(rule)
        if re.search(r'\b(?:no|not|never|without|neither|denied|denies)\b', clause, re.I) and re.search(r'FDA|approval|clinical hold|CRL|Complete Response', clause, re.I) and len(found) == before:
            excluded.append('explicit_absence')
    if found:
        chosen = max(found, key=lambda r: r['priority'])
        result.update({k: chosen[k] for k in ('event_family', 'event_outcome', 'polarity', 'confirmed_outcome', 'needs_review', 'uncertainty_status')})
        result['material'] = True
        outcomes = {r['event_outcome'] for r in found}
        if {'primary_endpoint_met', 'primary_endpoint_failed'} <= outcomes:
            multiple = len(set(re.findall(r'\b(?:Drug|Disease)[A-Z]\w*', sample))) > 1
            result.update(polarity='mixed', event_outcome='multiple_programs' if multiple else 'conflicting_results',
                          confirmed_outcome=False, needs_review=True,
                          uncertainty_status='multiple_subjects' if multiple else 'conflicting')
        elif 'primary_endpoint_failed' in outcomes and 'secondary_endpoint_met' in outcomes:
            result.update(polarity='mixed', uncertainty_status='mixed_endpoints')
        elif {'numerical_trend', 'inconclusive_results'} <= outcomes:
            result.update(polarity='mixed')
        elif {'positive', 'negative'} <= {r['polarity'] for r in found}:
            result.update(polarity='mixed', confirmed_outcome=False, needs_review=True, uncertainty_status='mixed_results')
        if outcomes == {'numerical_trend'}:
            result.update(polarity='mixed')
        if 'explicit_absence' in excluded and result['polarity'] == 'positive':
            result.update(polarity='mixed', confirmed_outcome=False, needs_review=True, uncertainty_status='conflicting')
        # Trial authorization takes precedence over an IDE described as approved.
        authorization = next((r for r in found if r['event_outcome'] == 'trial_authorization'), None)
        if authorization:
            result.update({k: authorization[k] for k in ('event_family', 'event_outcome', 'polarity', 'confirmed_outcome', 'needs_review', 'uncertainty_status')})
    else:
        status = next((s for s in ('subject_scope', 'ambiguous_negation', 'explicit_absence', 'historical', 'conditional', 'prospective') if s in excluded), None)
        if status:
            family = 'clinical_outcome' if status == 'ambiguous_negation' else 'regulatory_context'
            outcome = {'subject_scope': 'unattributed_reference', 'explicit_absence': 'explicit_absence', 'historical': 'historical_reference'}.get(status, 'unconfirmed')
            if status in {'prospective', 'conditional'}:
                family = 'conditional_milestone' if re.search(r'FDA|approval|Voucher', sample, re.I) else 'clinical_catalyst'
                outcome = 'potential_prv' if re.search(r'Voucher', sample, re.I) else 'prospective_approval' if family == 'conditional_milestone' else 'upcoming_readout' if re.search(r'results|readout', sample, re.I) else 'prospective_result'
            result.update(event_family=family, event_outcome=outcome, uncertainty_status=status,
                          needs_review=status in {'subject_scope', 'ambiguous_negation'}, material=status == 'ambiguous_negation')
        elif re.search(r'positive\s+(?:Phase|top.?line|results)|(?:Phase\s*[23]|top.?line).{0,60}(?:results|data)|Phase\s*[23]\s+top.?line|pivotal\s+trial\s+results|interim.{0,35}analysis|FDA\s+decision', sample, re.I):
            result.update(event_family='clinical_outcome', event_outcome='results_reported', material=True, needs_review=True, uncertainty_status='unspecified_endpoint')
    if re.search(r'CVR|contingent value right', sample, re.I) and not result['confirmed_outcome']:
        result.update(event_family='corporate_action', event_outcome='conditional_cvr', polarity='unknown', material=True,
                      needs_review=False, uncertainty_status='conditional')
    if re.search(r'LawFirm|law firm|securities claims|class.action', sample, re.I):
        result.update(event_family='corporate_action', event_outcome='legal_context', polarity='unknown', material=True,
                      confirmed_outcome=False, needs_review=True, uncertainty_status='background_context')
    if re.search(r'\bone\s+indication\b', sample, re.I) and re.search(r'\banother\b', sample, re.I) and re.search(r'Complete Response Letter|CRL', sample, re.I):
        result.update(polarity='mixed', event_outcome='multiple_programs', confirmed_outcome=False, needs_review=True, uncertainty_status='multiple_subjects')
    return result
