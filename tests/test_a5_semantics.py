import json
from pathlib import Path

import pytest

from mozes.alert_semantics import classify_evidence
from mozes.alert_explanation import explain, normalize_source, _optional_ai
from mozes.materiality import alert_priority
import hashlib

CORPUS = json.loads((Path(__file__).parent / 'fixtures/a5/alert_evidence_corpus.json').read_text())


@pytest.mark.parametrize('case', CORPUS['cases'], ids=lambda c: c['id'])
def test_adversarial_evidence_contract(case):
    actual = classify_evidence(case['text'], case['basis'], as_of='2026-10-12')
    assert {key: actual[key] for key in case['expected']} == case['expected']


@pytest.mark.parametrize('text', [
    'No product developed by the company and its partners for any indication has been approved by the U.S. FDA.',
    'If FDA approved the NDA, payments would become due.',
    'FDA has not issued a new Complete Response Letter.',
    '<script>FDA approved the NDA.</script><p>Routine administrative update.</p>',
    'FDA approved the NDA ' + 'for the defined patient population ' * 500 + 'if the future conditions are met.',
])
def test_scope_and_truncation_cannot_manufacture_completed_outcome(text):
    assert classify_evidence(text, 'source_text')['confirmed_outcome'] is False


def test_negative_result_survives_separate_future_company_plan():
    outcome = classify_evidence('The trial failed to meet its primary endpoint and the company will review the dataset.', 'source_text')
    assert outcome['polarity'] == 'negative' and outcome['confirmed_outcome']


def test_verified_nonwatch_issuer_can_be_urgent_but_watch_preference_cannot():
    actual = classify_evidence('FDA approved the NDA for DrugX.', 'source_text')
    assert alert_priority('fda_release_signal','high',actual,ticker='ZZZZ',watched=False,issuer_verified=True)=='P1'
    assert alert_priority('fda_release_signal','high',actual,ticker='ZZZZ',watched=True,issuer_verified=False)=='P2'
    conditional = classify_evidence('Under the CVR Agreement, payments occur if FDA approval is granted.', 'source_text')
    assert alert_priority('sec_material_filing','critical',conditional,ticker='ZZZZ',watched=True,issuer_verified=True)=='P2'


@pytest.mark.parametrize('prefix', ['No ', 'If '])
def test_complete_source_quote_keeps_long_antecedent_and_offsets(prefix):
    text = prefix + 'the company and all of its partners in every development program ' * 3 + 'have FDA approval for DrugX.'
    digest = hashlib.sha256(text.encode()).hexdigest()
    x = explain({'new_value':{},'source_url':'https://www.sec.gov/source.htm'},text,basis='source_text',source_hash=digest)
    evidence=x['evidence'][0]
    assert evidence['quote']==text and evidence['quote'].startswith(prefix)
    assert text[evidence['start']:evidence['end']]==evidence['quote'] and evidence['source_hash']==digest
    oversized=explain({'new_value':{}},prefix+'word '*150+'FDA approval.',basis='source_text')
    assert not oversized['evidence'] and oversized['missing_he']


def test_ai_wording_fails_closed_without_calling_provider(monkeypatch):
    monkeypatch.setenv('MOZES_ALERT_AI','1')
    class Provider:
        def complete(self,*args):
            raise AssertionError('Unproven paraphrasing must not be called')
    original=explain({'new_value':{}},'No product has been approved by FDA.',basis='source_text')
    result=_optional_ai(original,Provider())
    assert result['summary_he']==original['summary_he'] and result['ai_status']=='fallback'


def test_atos_raw_fixture_provenance_and_conditional_body():
    directory=Path(__file__).parent/'fixtures/a5'
    raw=(directory/'atos-ex99-1.html').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='ccaa408dd2adc1964a24a5f4124075e52c5b6989eb3bc5d6d97e2e89f30e6bae'
    assert hashlib.sha256((directory/'atos-8k.html').read_bytes()).hexdigest()=='ad245669d2c68d126ef5b3ae2f08bb6f14e17e85bb6953f0efbb3ce629e9cdfb'
    outcome=classify_evidence(normalize_source(raw),'source_text')
    assert outcome['polarity']=='unknown' and not outcome['confirmed_outcome']


def test_dated_history_and_indication_boilerplate_are_not_new_decisions():
    historic=classify_evidence('In September 2023, we announced positive topline Phase 2 results.','source_text')
    assert historic['uncertainty_status']=='historical' and not historic['material']
    body=classify_evidence('An athlete awareness campaign launched today.\nIndications and Important Safety Information\nIndications Approved by the U.S. FDA','source_text')
    assert body['polarity']=='unknown' and not body['confirmed_outcome']


def test_mixed_quotes_preserve_both_program_results_without_splicing():
    text='DrugA met its primary endpoint. DrugB failed to meet its primary endpoint.'
    result=explain({'new_value':{}},text,basis='source_text')
    assert result['source_polarity']=='mixed'
    assert [e['quote'] for e in result['evidence']]==['DrugA met its primary endpoint.','DrugB failed to meet its primary endpoint.']


@pytest.mark.parametrize('reference',['2026-06-20T12:00:00Z','Sat, 20 Jun 2026 12:00:00 GMT','2026-07-01T00:30:00+03:00'])
def test_temporal_scope_uses_source_publication_not_later_wall_clock(reference):
    text='FDA approved DrugX in June 2026.'
    assert classify_evidence(text,'source_text',as_of=reference)['confirmed_outcome']
    assert not classify_evidence(text,'source_text',as_of='2026-10-12')['confirmed_outcome']


@pytest.mark.parametrize('text',['FDA approved the clinical trial protocol.','FDA approved the manufacturing facility.','FDA approved the application for review.'])
def test_procedural_authorization_is_not_marketing_approval(text):
    outcome=classify_evidence(text,'source_text')
    assert outcome['event_family']=='regulatory_milestone' and outcome['polarity']=='unknown'
    assert not outcome['confirmed_outcome']


@pytest.mark.parametrize('text',[
    'No product developed by the company\nhas been approved by FDA.',
    '<p>No product developed by the company\nhas been approved by FDA.</p>',
    'If the application succeeds; FDA approved the NDA.',
    'FDA approved the NDA. No product has been approved by FDA.',
    '<p>&#78;o product has been approved by FDA.</p>',
    '<p>&#x49;f FDA approved DrugX, payments would be due.</p>',
])
def test_layout_and_conditional_scope_cannot_create_a_verified_decision(text):
    outcome=classify_evidence(text,'source_text')
    assert not outcome['confirmed_outcome']


def test_soft_html_linebreak_cannot_drop_no_from_source_quote():
    raw='<p>No product developed by the company\nhas been approved by FDA.</p>'
    text=normalize_source(raw)
    result=explain({'new_value':{}},text,basis='source_text')
    assert result['evidence'][0]['quote']=='No product developed by the company has been approved by FDA.'
    assert result['source_polarity']=='unknown'


def test_bounded_read_cannot_cut_at_an_abbreviation_before_a_condition():
    text='FDA approved DrugX for U.S. patients ' + 'under the defined treatment conditions '*500+' if the application succeeds.'
    outcome=classify_evidence(text,'source_text')
    assert not outcome['confirmed_outcome'] and outcome['needs_review']
    assert outcome['uncertainty_status']=='truncated_source'
