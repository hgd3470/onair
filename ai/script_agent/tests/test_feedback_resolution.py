import json

import httpx
import pytest

from script_agent.io import atomic_json
from script_agent.models import BriefReviewResult, EditorialCheck, Handoff, RevisionResult, SourceMeta, Probe
from script_agent.pipeline import validate_editorial_brief
from script_agent.providers import APIProvider, ProviderError
from test_pipeline import setup_pipeline, review, flow_draft


def audited(satisfied=True, requirement=1):
    return BriefReviewResult(editorial_checks=[EditorialCheck(
        requirement=requirement, satisfied=satisfied,
        evidence='문장 1과 마지막 문장의 핵심 연결 확인' if satisfied else '마지막 문장에 요청한 요점이 없음')],
        review=review('pass'))


def test_failed_brief_forces_revision_and_null_keeps_original_material(tmp_path):
    revised = flow_draft()
    pipe, c, draft, generator, reviewer = setup_pipeline(
        tmp_path, [audited(False), audited()], [RevisionResult(draft=revised, material=None)])
    brief = ['도입의 질문을 마지막 문장에서 회수한다.']
    out = pipe.run(c['source_text'], SourceMeta.model_validate(c['metadata']), 'a'*64,
                   tmp_path/'out', editorial_brief=brief)
    assert out['generator']['revision'] == 1
    assert out['status'] == 'ready_for_downstream_review'
    assert out['material'] == c['material']
    assert out['sources'][0]['text'] == c['source_text']
    assert out['editorial_brief'] == brief
    assert out['self_review'][0]['verdict'] == 'revise'
    assert out['self_review'][0]['issues'][0]['problem'] == '사용자 피드백 미해결'
    assert all(p['editorial_brief'] == brief for p in generator.payloads + reviewer.payloads)
    assert out['self_review'][-1]['editorial_checks'][0]['satisfied']
    Handoff.model_validate(out)


@pytest.mark.parametrize('checks', [[audited().editorial_checks[0]],
    [audited().editorial_checks[0], audited().editorial_checks[0]],
    [audited(requirement=3).editorial_checks[0]]])
def test_missing_duplicate_or_wrong_requirement_cannot_pass(tmp_path, checks):
    result = audited(); result.editorial_checks = checks
    pipe, c, _, _, _ = setup_pipeline(tmp_path, [result])
    pipe.config['pipeline']['max_revisions'] = 0
    out = pipe.run(c['source_text'], SourceMeta.model_validate(c['metadata']), 'b'*64,
                   tmp_path/'out', editorial_brief=['도입 장면', '결말 요점'])
    assert out['status'] == 'hold'
    assert out['self_review'][0]['issues'][0]['problem'] == '사용자 피드백 확인 누락'


def test_external_revision_retains_editorial_brief(tmp_path):
    pipe, c, _, generator, reviewer = setup_pipeline(tmp_path, [audited(), audited()],
        [RevisionResult(draft=flow_draft(), material=None)])
    out = pipe.run(c['source_text'], SourceMeta.model_validate(c['metadata']), 'c'*64,
                   tmp_path/'out', editorial_brief=['끝에서 질문 회수'])
    revised = pipe.apply_feedback(tmp_path/'out'/f"{out['content_id']}.json",
                                  [review().issues[0]], out['version'])
    assert revised['editorial_brief'] == ['끝에서 질문 회수']
    assert revised['generator']['revision'] == 0
    assert revised['generator']['feedback_revision'] == 1
    assert reviewer.payloads[-1]['editorial_brief'] == revised['editorial_brief']


@pytest.mark.parametrize('value', ['문자열', {'C07': []}, [''], [None], ['x'*4001]])
def test_invalid_brief_rejected(value):
    with pytest.raises(ValueError, match='invalid_editorial_brief'):
        validate_editorial_brief(value)


@pytest.mark.parametrize('reason', ['MAX_TOKENS', 'SAFETY', 'fake-secret-sentinel'])
def test_incomplete_diagnostics_preserve_reason_not_body_or_unknown_values(monkeypatch, reason):
    secret = 'fake-secret-sentinel'
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(200, json={'candidates': [{'finishReason': reason,
            'content': {'parts': [{'text': secret}]}, 'finishMessage': secret}],
            'promptFeedback': {'blockReason': secret},
            'usageMetadata': {'thoughtsTokenCount': 16000, 'totalTokenCount': 18000,
                              'candidatesTokenCount': secret, 'other': secret}})
    monkeypatch.setattr(httpx, 'post', post)
    provider = APIProvider({'provider':'gemini','model':'configured','effort':'medium',
                            'max_output_tokens':16000}, {'gemini_api_key':secret})
    with pytest.raises(ProviderError) as caught:
        provider.complete('prompt', {}, Probe)
    assert len(calls) == 1  # No automatic retry of blocked or truncated content.
    assert caught.value.diagnostics['finish_reason'] == (reason if reason != secret else 'UNKNOWN')
    assert caught.value.diagnostics['token_counts'] == {'thoughtsTokenCount':16000, 'totalTokenCount':18000}
    assert secret not in json.dumps(caught.value.diagnostics)


def test_failed_call_logs_safe_diagnostics_and_can_resume(tmp_path):
    error = ProviderError('openai', 'configured', 'incomplete_or_refused',
                          diagnostics={'finish_reason':'MAX_TOKENS'})
    pipe, c, _, _, reviewer = setup_pipeline(tmp_path, [error, review('pass')])
    meta = SourceMeta.model_validate(c['metadata'])
    with pytest.raises(ProviderError):
        pipe.run(c['source_text'], meta, 'd'*64, tmp_path/'out')
    errors = list((tmp_path/'out/logs').glob('*/*-error-*.json'))
    assert len(errors) == 1
    assert json.loads(errors[0].read_text())['diagnostics']['finish_reason'] == 'MAX_TOKENS'
    out = pipe.run(c['source_text'], meta, 'd'*64, tmp_path/'out')
    assert out['status'] == 'ready_for_downstream_review'
    assert errors[0].exists()


def material_with_quote(quote):
    from script_agent.models import Fact, Material
    from test_pipeline import fixture
    m = Material.model_validate(fixture()['material'])
    m.facts = [Fact(id='f1', text='자체 작성 시험 사실', quote=quote,
                    kind='source_statement', conditions=[])]
    return m


def test_quote_alignment_recovers_exact_source_and_keeps_original_model_value():
    from script_agent.validation import align_material_quotes
    text = 'The **silver forest** contains *Fictitious trees* and white\u00a0stones.'
    quote = 'The silver forest contains Fictitious trees and white stones.'
    original = material_with_quote(quote)
    fixed, notes = align_material_quotes(original, text)
    assert original.facts[0].quote == quote
    assert fixed.facts[0].quote == text
    assert notes[0]['source_quote'] == text[notes[0]['source_start']:notes[0]['source_end']]
    assert fixed.facts[0].text == original.facts[0].text


@pytest.mark.parametrize('source,quote', [
    ('The test found 91 percent improvement.', 'The test found 98 percent improvement.'),
    ('They left.\n2 They returned the next day.', 'They left. They returned the next day.'),
    ('The author did not see the result.', 'The author did see the result.'),
    ('A test uses 2*3 plus four test terms.', 'A test uses 23 plus four test terms.'),
    ('The **silver forest** is quiet. The *silver forest* is quiet.', 'The silver forest is quiet.'),
])
def test_alignment_does_not_repair_semantics_verse_markers_math_or_ambiguous_matches(source, quote):
    from script_agent.validation import align_material_quotes
    fixed, notes = align_material_quotes(material_with_quote(quote), source)
    assert fixed.facts[0].quote == quote and not notes


def test_pipeline_records_literal_quote_recovery_without_editing_source_or_model_log(tmp_path):
    from script_agent.models import Material
    from test_pipeline import Fake
    source = 'The **silver forest** contains three imaginary trees.'
    quote = 'The silver forest contains three imaginary trees.'
    material = material_with_quote(quote)
    pipe, c, draft, generator, reviewer = setup_pipeline(tmp_path, [review('pass')])
    pipe.config['pipeline']['max_revisions'] = 0
    generator.values = [material, draft]
    out = pipe.run(source, SourceMeta.model_validate(c['metadata']), 'e'*64, tmp_path/'out')
    assert out['material']['facts'][0]['quote'] == source
    assert out['sources'][0]['text'] == source
    logs = tmp_path/'out/logs'/out['content_id']
    assert json.loads((logs/'01-analyze-result.json').read_text())['result']['facts'][0]['quote'] == quote
    assert json.loads((logs/'analysis-quote-alignment.json').read_text())[0]['source_quote'] == source


@pytest.mark.parametrize('error,kind', [(httpx.ReadTimeout,'timeout'), (httpx.ConnectError,'network_error')])
def test_transport_diagnostic_is_specific_and_does_not_retry_or_leak(monkeypatch, error, kind):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        raise error('fake-secret-request-header')
    monkeypatch.setattr(httpx, 'post', post)
    provider = APIProvider({'provider':'gemini','model':'configured','effort':'medium',
                            'max_output_tokens':32000}, {'gemini_api_key':'fake-secret'})
    with pytest.raises(ProviderError) as caught:
        provider.complete('prompt', {}, Probe)
    assert caught.value.code == 'transport_or_timeout'
    assert caught.value.diagnostics == {'transport_failure':kind}
    assert len(calls) == 1
    assert 'fake-secret' not in str(caught.value)


@pytest.mark.parametrize('status,reason,refusal', [
    ('incomplete', 'max_output_tokens', False),
    ('incomplete', 'content_filter', False),
    ('failed', 'fake-secret-sentinel', False),
    ('fake-secret-sentinel', None, False),
    ('completed', None, True),
])
def test_openai_failure_diagnostics_are_bounded_and_never_retry(monkeypatch, status, reason, refusal):
    secret = 'fake-secret-sentinel'
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(200, json={
            'status': status, 'incomplete_details': {'reason': reason} if reason else None,
            'output': [{'content': [{'type': 'refusal' if refusal else 'output_text',
                                     'refusal': secret, 'text': secret}]}],
            'error': {'message': secret},
            'usage': {'input_tokens': 500, 'output_tokens': 12000, 'total_tokens': 12500,
                      'unknown': secret, 'output_tokens_details': {'reasoning_tokens': 11900,
                                                                 'other': secret}},
        })
    monkeypatch.setattr(httpx, 'post', post)
    provider = APIProvider({'provider': 'openai', 'model': 'configured', 'effort': 'medium',
                            'max_output_tokens': 12000}, {'openai_api_key': secret})
    with pytest.raises(ProviderError) as caught:
        provider.complete('prompt', {}, Probe)
    assert len(calls) == 1
    assert caught.value.code == 'incomplete_or_refused'
    d = caught.value.diagnostics
    assert d['response_status'] == (status if status != secret else 'UNKNOWN')
    assert d['incomplete_reason'] == (reason if reason in {'max_output_tokens', 'content_filter'} else 'UNKNOWN')
    assert d['refusal_present'] is refusal
    assert d['token_counts'] == dict(input_tokens=500, output_tokens=12000, total_tokens=12500,
                                     reasoning_tokens=11900)
    assert secret not in json.dumps(d)


def test_openai_failure_with_null_usage_is_logged(monkeypatch):
    monkeypatch.setattr(httpx, 'post', lambda *a, **k: httpx.Response(200, json={
        'status': 'incomplete', 'incomplete_details': None, 'usage': None, 'output': []}))
    provider = APIProvider({'provider': 'openai', 'model': 'configured', 'effort': 'medium',
                            'max_output_tokens': 12000}, {'openai_api_key': 'fake-key'})
    with pytest.raises(ProviderError) as caught:
        provider.complete('prompt', {}, Probe)
    assert caught.value.diagnostics['token_counts'] == {}
    assert caught.value.diagnostics['incomplete_reason'] == 'UNKNOWN'
