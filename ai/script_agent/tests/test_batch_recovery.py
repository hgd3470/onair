"""No-network regressions for durable batch submission, replay and failure isolation."""
import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import yaml

from script_agent import batch, pipeline as pipeline_module
from script_agent.io import load_config
from script_agent.models import Claim, Draft, Material, Review, RevisionResult
from script_agent.pipeline import Pipeline
from script_agent.providers import Completion, ProviderError

ROOT = Path(__file__).resolve().parents[1]


class Interrupted(BaseException):
    pass


@pytest.fixture
def harness(tmp_path, monkeypatch):
    # An accidental unmocked request must fail locally, never read .env or call APIs.
    monkeypatch.setattr(httpx, 'post', Mock(side_effect=AssertionError('unexpected HTTP')))
    monkeypatch.setattr(httpx, 'get', Mock(side_effect=AssertionError('unexpected HTTP')))
    shutil.copytree(ROOT / 'prompts', tmp_path / 'prompts')
    cfg = load_config(ROOT)
    cfg['batch'] = dict(batch_size=10, poll_interval_sec=1, gpt_workers=2)
    case = json.loads((ROOT / 'examples/reviewer_cases/adaptation.json').read_text())
    mat = Material.model_validate(case['material'])
    draft = Draft.model_validate(case['draft'])
    draft.script = [
        '돈을 주려다 사이가 틀어질 뻔했어요.', '이 이야기는 가상 사연입니다.',
        '민수는 두 달간 친구에게 신세를 졌어요.', '이제 떠나며 월세를 나눠 내고 싶었죠.',
        '친구는 그 제안을 받아들이지 않았어요.', '민수는 돈부터 보내 놓을까 망설였어요.',
        '그러고는 연락을 막아 둘 생각도 했죠.', '하지만 둘 다 생각에만 그쳤습니다.',
        '친구의 속내는 아직 알 수 없어요.',
    ]
    ids = [[], [], ['f1'], ['f1'], ['f1'], ['f2'], ['f2'], ['f3'], []]
    draft.claims = [Claim(sentence=i, kind='source_statement' if f else 'transition', fact_ids=f)
                    for i, f in enumerate(ids, 1)]
    h = SimpleNamespace(root=tmp_path, cfg=cfg, material=mat, draft=draft, case=case,
                        batches={}, creates=[], polls=[], reviews=[], revision_target=1,
                        response_hook=None, poll_hook=None)

    def add_source(name):
        (tmp_path / 'input').mkdir(exist_ok=True)
        meta = {**case['metadata'], 'source_id': name}
        (tmp_path / 'input' / f'{name}.md').write_text(
            '---\n' + yaml.safe_dump(meta, allow_unicode=True) + '---\n' + case['source_text'])
    h.add_source = add_source
    add_source('source-a')

    class Reviewer:
        def complete(self, system, payload, schema):
            h.reviews.append(copy.deepcopy(payload))
            r = Review(verdict='revise' if payload['revision'] < h.revision_target else 'pass',
                       issues=[], scores=dict(grounding=4, listening=4, engagement=4, type_fit=4),
                       strengths=[], remaining=[])
            return Completion(r, {'test_tokens': 7}, 0.1, 'mock-reviewer')

    def pipeline():
        return Pipeline(tmp_path, cfg, {}, providers={
            'generator': SimpleNamespace(keys={'gemini_api_key': 'fake'}), 'reviewer': Reviewer()})
    h.pipeline = pipeline
    h.run = lambda limit=100: batch.process_batch_queue(tmp_path, pipeline(), limit)
    h.outputs = lambda: [json.loads(p.read_text()) for p in (tmp_path / 'output').rglob('S2-*.json')]

    def create(self, model, requests, display_name):
        name = f'batches/{len(h.creates)}'
        h.creates.append(display_name.split('-')[0])
        h.batches[name] = (display_name.split('-')[0], copy.deepcopy(requests))
        return name

    def poll(self, name, **kwargs):
        h.polls.append(name)
        if h.poll_hook:
            h.poll_hook(name)
        stage, requests = h.batches[name]
        items = []
        for req in requests:
            payload = json.loads(req['request']['contents'][0]['parts'][0]['text'])
            if stage == 'analyze':
                value = mat.model_dump()
            elif stage == 'generate':
                value = draft.model_dump()
            else:
                changed = Material.model_validate(payload['material'])
                changed.facts[0].text += ' 수정됨'
                value = RevisionResult(material=changed, draft=Draft.model_validate(payload['draft'])).model_dump()
            if h.response_hook:
                value = h.response_hook(stage, payload, value)
            if isinstance(value, dict) and 'error' in value:
                item = value
            else:
                item = {'response': {'candidates': [{'finishReason': 'STOP', 'content': {
                    'parts': [{'text': json.dumps(value, ensure_ascii=False)}]}}]}}
            items.append({'metadata': req['metadata'], **item})
        return {'state': 'BATCH_STATE_SUCCEEDED', 'response': {'inlinedResponses': {'inlinedResponses': items}}}

    monkeypatch.setattr(batch.GeminiBatchClient, 'create_batch', create)
    monkeypatch.setattr(batch.GeminiBatchClient, 'poll_batch', poll)
    return h


@pytest.mark.parametrize('failed_revision', [1, 2])
def test_failed_revision_never_packages_stale_output(harness, failed_revision):
    h = harness
    h.revision_target = 2
    def fail(stage, payload, value):
        if stage == 'revise' and len(payload['previous_reviews']) == failed_revision:
            return {'error': {'code': 3, 'message': 'mock failure'}}
        return value
    h.response_hook = fail
    results = h.run()
    assert len(results) == 1 and results[0]['status'] == 'failed'
    assert results[0]['code'] == 'batch_item_error'
    assert h.outputs() == []
    assert len(list((h.root / 'failed').glob('*/job.json'))) == 1
    assert not list((h.root / 'processing').glob('*/job.json'))
    assert [r['revision'] for r in h.reviews] == list(range(failed_revision))


def test_invalid_revision_isolated_from_healthy_job(harness):
    h = harness
    h.add_source('source-b')
    def corrupt(stage, payload, value):
        if stage == 'revise' and payload['metadata']['source_id'] == 'source-a':
            value['material']['facts'][0]['quote'] = '원문에 없는 인용'
        return value
    h.response_hook = corrupt
    results = h.run()
    assert sorted(r['status'] for r in results) == ['completed', 'failed']
    assert next(r for r in results if r['status'] == 'failed')['code'] == 'fact_quote_not_in_source'
    assert len(h.outputs()) == 1
    assert h.outputs()[0]['input_metadata']['source_id'] == 'source-b'


@pytest.mark.parametrize('cut', ['04-revise-result.json', '05-review-result.json', 'revision-1.json'])
def test_resume_restores_material_and_does_not_repeat_calls(harness, monkeypatch, cut):
    h = harness
    original = batch.atomic_json
    def interrupt(path, value):
        original(path, value)
        if path.name == cut:
            raise Interrupted()
    with monkeypatch.context() as m:
        m.setattr(batch, 'atomic_json', interrupt)
        m.setattr(pipeline_module, 'atomic_json', interrupt)
        with pytest.raises(Interrupted):
            h.run()
    creates = list(h.creates)
    result = h.run()
    assert result[0]['status'] == 'completed'
    assert h.creates == creates
    assert len(h.reviews) == 2
    out = h.outputs()[0]
    assert out['material'] == h.reviews[-1]['material']
    assert out['material']['facts'][0]['text'].endswith(' 수정됨')
    assert out['generator']['revision'] == 1
    assert out['generator']['feedback_revision'] == 0
    assert [r['revision'] for r in out['self_review']] == [0, 1]
    assert [e['stage'] for e in out['generator']['calls']] == ['analyze', 'generate', 'review', 'revise', 'review']


def test_poll_failure_resumes_same_remote_job_even_with_smaller_limit(harness):
    h = harness
    h.add_source('source-b')
    h.poll_hook = lambda name: (_ for _ in ()).throw(httpx.ReadTimeout('mock'))
    with pytest.raises(httpx.ReadTimeout):
        h.run()
    assert h.creates == ['analyze']
    h.poll_hook = None
    assert h.run(limit=1)[0]['status'] == 'completed'
    assert h.run(limit=1)[0]['status'] == 'completed'
    assert h.creates.count('analyze') == 1
    assert len(h.outputs()) == 2


@pytest.mark.parametrize('change', ['model', 'prompt', 'schema'])
def test_changed_checkpoint_rejected_without_api(harness, monkeypatch, change):
    h = harness
    h.poll_hook = lambda name: (_ for _ in ()).throw(Interrupted())
    with pytest.raises(Interrupted):
        h.run()
    creates = list(h.creates)
    if change == 'model':
        h.cfg['models']['generator']['model'] = 'different-model'
    elif change == 'prompt':
        path = h.root / 'prompts' / h.cfg['pipeline']['prompt_version'] / 'review.txt'
        path.write_text(path.read_text() + '\nchanged')
    else:
        original = Material.model_json_schema
        monkeypatch.setattr(Material, 'model_json_schema', lambda **kw: {**original(**kw), 'description': 'changed'})
    results = h.run()
    assert results[0]['code'] == 'checkpoint_config_changed_use_new_destination'
    assert h.creates == creates
    assert h.outputs() == []


def test_parallel_reviews_have_complete_per_job_events(harness):
    h = harness
    h.add_source('source-b')
    assert all(r['status'] == 'completed' for r in h.run())
    for out in h.outputs():
        assert [e['stage'] for e in out['generator']['calls']] == ['analyze', 'generate', 'review', 'revise', 'review']
        log = h.root / 'logs' / out['content_id']
        assert sorted(p.name for p in log.glob('*-review-request.json')) == ['03-review-request.json', '05-review-request.json']
        assert out['material'] == json.loads((log / '05-review-request.json').read_text())['payload']['material']


def test_unknown_submission_is_not_automatically_resubmitted(harness, monkeypatch):
    h = harness
    create = Mock(side_effect=httpx.ReadTimeout('response lost'))
    monkeypatch.setattr(batch.GeminiBatchClient, 'create_batch', create)
    with pytest.raises(httpx.ReadTimeout):
        h.run()
    with pytest.raises(ProviderError, match='batch_submission_unknown'):
        h.run()
    assert create.call_count == 1
    assert h.outputs() == []


def test_partial_pointer_write_recovers_without_duplicate_submission(harness, monkeypatch):
    h = harness
    h.add_source('source-b')
    original = batch.atomic_json
    def interrupt(path, value):
        original(path, value)
        if path.name == '01-analyze-batch.json':
            raise Interrupted()
    with monkeypatch.context() as m:
        m.setattr(batch, 'atomic_json', interrupt)
        m.setattr(pipeline_module, 'atomic_json', interrupt)
        with pytest.raises(Interrupted):
            h.run()
    assert h.creates == []
    assert all(r['status'] == 'completed' for r in h.run())
    assert h.creates.count('analyze') == 1


def test_client_create_timeout_has_no_post_retry(monkeypatch):
    post = Mock(side_effect=httpx.ReadTimeout('lost reply'))
    monkeypatch.setattr(httpx, 'post', post)
    with pytest.raises(ProviderError):
        batch.GeminiBatchClient('fake').create_batch('mock', [])
    assert post.call_count == 1


def test_two_revisions_preserve_separate_counters_and_all_calls(harness):
    h = harness
    h.revision_target = 2
    assert h.run()[0]['status'] == 'completed'
    out = h.outputs()[0]
    assert out['version'] == '1.0.2'
    assert out['generator']['revision'] == 2
    assert out['generator']['feedback_revision'] == 0
    assert [r['revision'] for r in out['self_review']] == [0, 1, 2]
    assert [e['stage'] for e in out['generator']['calls']] == [
        'analyze', 'generate', 'review', 'revise', 'review', 'revise', 'review']


def test_tampered_stage_request_is_rejected_without_overwriting(harness, monkeypatch):
    h = harness
    original = batch.atomic_json
    def interrupt(path, value):
        original(path, value)
        if path.name == '02-generate-result.json':
            raise Interrupted()
    with monkeypatch.context() as m:
        m.setattr(batch, 'atomic_json', interrupt)
        with pytest.raises(Interrupted):
            h.run()
    path = next((h.root / 'logs').glob('*/02-generate-request.json'))
    data = json.loads(path.read_text())
    data['response_schema']['description'] = 'different schema'
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    creates = list(h.creates)
    results = h.run()
    assert results[0]['code'] == 'checkpoint_config_changed_use_new_destination'
    assert path.read_bytes() == before
    assert h.creates == creates
    assert h.outputs() == []


def test_revision_cannot_remove_existing_fact_ids(harness):
    h = harness
    def remove(stage, payload, value):
        if stage == 'revise':
            value['material']['facts'].pop()
        return value
    h.response_hook = remove
    assert h.run()[0]['code'] == 'revision_cannot_remove_fact_ids'
    assert h.outputs() == []


def test_null_material_revision_reuses_validated_material(harness):
    h = harness
    def unchanged(stage, payload, value):
        if stage == 'revise':
            value['material'] = None
        return value
    h.response_hook = unchanged
    assert h.run()[0]['status'] == 'completed'
    assert h.outputs()[0]['material'] == h.material.model_dump()


def test_chunks_are_submitted_before_polling(harness):
    h = harness
    h.add_source('source-b')
    h.cfg['batch']['batch_size'] = 1
    def verify(name):
        if h.batches[name][0] == 'analyze':
            assert h.creates.count('analyze') == 2
    h.poll_hook = verify
    assert all(r['status'] == 'completed' for r in h.run())


def test_news_batch_output_reserves_team_rights_and_preserves_source_rights(harness):
    from script_agent.news_context import LEGACY_HEADER, render_facts
    h=harness
    h.revision_target=0
    meta={**h.case['metadata'], 'source_id':'IC-test-news', 'author':'이슈 수집 에이전트',
          'source_kind':'user_summary', 'license':{'kind':'unknown'},
          'original_sources':[{'source_url':'https://example.org/news', 'author':'시험 언론사',
                               'license':{'kind':'unknown'}}]}
    text=LEGACY_HEADER+'\n'+render_facts([dict(id='F1', text=h.case['source_text'].replace('\n',' '),
        attribution='시험 기관', event_date='2026-11-13', kind='event')])
    (h.root/'input/source-a.md').write_text('---\n'+yaml.safe_dump(meta,allow_unicode=True)+'---\n'+text)
    h.run()
    out,=h.outputs()
    assert out['license']=='All rights reserved'
    assert any('© 틈(TEUM) 팀' in n for n in out['rights_notes'])
    assert out['sources'][0]['text']==text
    assert out['input_metadata']['license']['kind']=='unknown'
    assert out['input_metadata']['original_sources'][0]['license']['kind']=='unknown'
    assert out['rights_status']=='unverified' and out['status']=='hold'
