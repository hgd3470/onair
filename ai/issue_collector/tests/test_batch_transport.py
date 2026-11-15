"""Offline HTTP regressions; no real keys, original articles or model requests."""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from issue_collector import batch_transport as bt, llm, pipeline
from issue_collector.core import Selection, read, save, target_day


@pytest.fixture
def remote(monkeypatch):
    state = SimpleNamespace(posts=[], gets=[], outcome='success', value={'topics': [], 'coverage_notes': []},
                            file=False, download_fail=False)

    def post(url, **kwargs):
        assert url.endswith(':batchGenerateContent')
        assert kwargs['headers']['x-goog-api-key'] == 'test-secret'
        assert 'Authorization' not in kwargs['headers']
        state.posts.append((url, kwargs))
        if state.outcome == 'lost_post':
            raise httpx.ReadTimeout('private provider message')
        return httpx.Response(200, json={'name': 'batches/test-one'})

    def get(url, **kwargs):
        state.gets.append(url)
        req = state.posts[0][1]['json']['batch']['inputConfig']['requests']['requests'][0]
        item = {'metadata': req['metadata'], 'response': {'candidates': [{'finishReason': 'STOP',
                'content': {'parts': [{'text': json.dumps(state.value)}]},
                'groundingMetadata': {'groundingChunks': [{'web': {'uri': 'https://example.org/article'}}]}}],
                'modelVersion': 'gemini-4-hydrogen', 'usageMetadata': {'totalTokenCount': 10}}}
        if ':download' in url:
            if state.download_fail:
                raise httpx.ConnectError('private download error')
            return httpx.Response(200, text=json.dumps(item))
        if state.outcome == 'poll_error':
            return httpx.Response(503, text='private response')
        if state.outcome == 'pending':
            return httpx.Response(200, json={'metadata': {'state': 'JOB_STATE_RUNNING'}})
        if state.outcome == 'failed':
            return httpx.Response(200, json={'metadata': {'state': 'JOB_STATE_FAILED'},
                'error': {'message': 'test-secret'}, 'response': {'inlinedResponses': [item]}})
        items = [item, item] if state.outcome == 'duplicate' else [item]
        response = {'responsesFile': 'files/test-results'} if state.file else {'inlinedResponses': items}
        return httpx.Response(200, json={'metadata': {'state': 'JOB_STATE_SUCCEEDED'}, 'response': response})

    monkeypatch.setattr(httpx, 'post', post)
    monkeypatch.setattr(httpx, 'get', get)
    return state


def request(tmp_path, **kw):
    return bt.generate({'contents': [{'parts': [{'text': kw.pop('text', 'hello')}]}]},
        model='gemini-4-hydrogen', key='test-secret', path=tmp_path/'batch.json', wait_timeout=0, **kw)


def test_completed_batch_reused_without_http(tmp_path, remote):
    first = request(tmp_path)
    assert request(tmp_path) == first
    assert len(remote.posts) == len(remote.gets) == 1
    assert 'test-secret' not in (tmp_path/'batch.json').read_text()


@pytest.mark.parametrize('outcome', ['pending', 'poll_error'])
def test_poll_failure_resumes_same_remote_job(tmp_path, remote, outcome):
    remote.outcome = outcome
    with pytest.raises(bt.BatchPending): request(tmp_path)
    assert read(tmp_path/'batch.json')['name'] == 'batches/test-one'
    remote.outcome = 'success'
    assert request(tmp_path)['usageMetadata']['totalTokenCount'] == 10
    assert len(remote.posts) == 1


def test_ambiguous_submission_never_reposted(tmp_path, remote):
    remote.outcome = 'lost_post'
    for _ in range(2):
        with pytest.raises(bt.BatchError, match='batch_submission_unknown'): request(tmp_path)
    assert len(remote.posts) == 1 and not remote.gets


def test_changed_request_rejected_without_new_submission(tmp_path, remote):
    request(tmp_path)
    with pytest.raises(bt.BatchError, match='contract_changed'): request(tmp_path, text='changed')
    assert len(remote.posts) == 1


def test_failed_job_cannot_reuse_success_shaped_body(tmp_path, remote):
    remote.outcome = 'failed'
    for _ in range(2):
        with pytest.raises(bt.BatchError, match='batch_job_failed'): request(tmp_path)
    assert len(remote.posts) == len(remote.gets) == 1
    assert 'test-secret' not in (tmp_path/'batch.json').read_text()


def test_duplicate_responses_rejected(tmp_path, remote):
    remote.outcome = 'duplicate'
    with pytest.raises(bt.BatchError, match='missing_or_duplicate'): request(tmp_path)


def test_file_download_resume_does_not_resubmit_or_repoll(tmp_path, remote):
    remote.file = True
    remote.download_fail = True
    with pytest.raises(bt.BatchPending): request(tmp_path)
    remote.download_fail = False
    request(tmp_path)
    assert len(remote.posts) == 1
    assert len([url for url in remote.gets if ':download' not in url]) == 1


def models(tmp_path):
    (tmp_path/'prompts').mkdir(exist_ok=True)
    (tmp_path/'prompts/select.txt').write_text('Select verified news')
    settings = {'provider': 'gemini', 'model': 'gemini-4-hydrogen', 'effort': 'medium', 'max_output_tokens': 100}
    cfg = {'models': {'generator': settings, 'verifier': {'provider': 'openai'}},
           'collection': {'mode': 'batch', 'model_timeout_sec': 10}, 'batch': {'wait_timeout_sec': 0}}
    return llm.Models(tmp_path, cfg, {'gemini_api_key': 'test-secret',
        'google_application_credentials': 'unused.json'}, tmp_path/'logs')


def test_collector_structured_call_replays_pending_batch(tmp_path, remote):
    model = models(tmp_path)
    remote.outcome = 'pending'
    with pytest.raises(bt.BatchPending): model.call('select', {'candidates': []}, Selection)
    remote.outcome = 'success'
    assert model.call('select', {'candidates': []}, Selection).topics == []
    assert model.call('select', {'candidates': []}, Selection).topics == []
    assert len(remote.posts) == 1
    body = remote.posts[0][1]['json']['batch']['inputConfig']['requests']['requests'][0]['request']
    assert 'responseSchema' in body['generationConfig'] and '$defs' not in json.dumps(body['generationConfig'])


def test_collector_grounding_uses_batch_and_preserves_grounding(tmp_path, remote):
    model = models(tmp_path)
    value = model.search('정책', target_day('2026-11-12'))
    assert value['urls'] == ['https://example.org/article']
    req = remote.posts[0][1]['json']['batch']['inputConfig']['requests']['requests'][0]['request']
    assert req['tools'] == [{'google_search': {}}]
    assert not {'responseJsonSchema', 'responseSchema'} & set(req['generationConfig'])
    assert model.search('정책', target_day('2026-11-12')) == value
    assert len(remote.posts) == 1


def test_pending_stream_does_not_freeze_partial_manifest(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, 'credentials', lambda _: {'gemini_api_key': 'test-secret'})
    cfg = {'models': {'generator': {'provider': 'gemini'}},
           'collection': {'mode': 'batch', 'stream_workers': 1}}

    def stream(root, config, keys, day, name, folder, count):
        calls.append(name)
        if len(calls) == 1: raise bt.BatchPending('batch_polling_timeout')
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'summary.txt').write_text('done')
        return {'status': 'complete', 'items': [{}]}

    monkeypatch.setattr(pipeline, 'collect_stream', stream)
    day = target_day('2026-11-12')
    first, folder = pipeline.run(tmp_path, cfg, day, ['issue'], 1, 'batch')
    assert first['status'] == 'pending'
    assert not (folder/'issue/manifest.json').exists()
    second, _ = pipeline.run(tmp_path, cfg, day, ['issue'], 1, 'batch', resume=True)
    assert second['status'] == 'complete' and len(calls) == 2


def test_transport_copies_keep_independent_install_contract():
    path = Path(bt.__file__)
    other = path.parents[3]/'script_agent/src/script_agent/batch_transport.py'
    if other.exists(): assert path.read_bytes() == other.read_bytes()


def test_pending_extraction_leaves_actual_stream_unfinished(tmp_path, monkeypatch):
    from issue_collector.core import Topic
    articles = [{'id': 'a', 'origin': 'one', 'url': 'https://www.hani.co.kr/one'},
                {'id': 'b', 'origin': 'two', 'url': 'https://www.hani.co.kr/two'}]
    topic = Topic(id='policy', title='정책 발표', reason='공공 영향', importance=4,
                  candidate_ids=['a','b'], search_query='정책')

    class PendingModels:
        def __init__(self, *args): pass
        def call(self, stage, *args):
            if stage == 'select': return Selection(topics=[topic], coverage_notes=[])
            raise bt.BatchPending('batch_polling_timeout')

    monkeypatch.setattr(pipeline, 'Models', PendingModels)
    monkeypatch.setattr(pipeline, 'discover', lambda *args: articles)
    monkeypatch.setattr(pipeline, 'acquire', lambda fetcher, article, *args: article)
    monkeypatch.setattr(pipeline, 'search_news', lambda *args, **kw: [])
    cfg = {'collection': {'http_timeout_sec': 1, 'http_workers': 1, 'candidate_topics': 1,
        'max_articles_per_topic': 2, 'min_independent_sources': 2, 'google_search': False}}
    with pytest.raises(bt.BatchPending):
        pipeline.collect_stream(tmp_path, cfg, {}, target_day('2026-11-12'), 'issue', tmp_path/'run', 1)
    assert not (tmp_path/'run/manifest.json').exists()
