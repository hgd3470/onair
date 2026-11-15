"""Single-file generation and external revision share durable Gemini batches."""
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from script_agent import pipeline, batch
from script_agent.io import load_config
from script_agent.providers import Completion, ProviderError

ROOT = Path(__file__).resolve().parents[1]


class Result(BaseModel):
    ok: bool


@pytest.fixture
def calls(tmp_path, monkeypatch):
    state = SimpleNamespace(posts=[], gets=[], pending=True, realtime=[])
    cfg = load_config(ROOT)
    cfg['batch']['wait_timeout_sec'] = 0
    pipe = pipeline.Pipeline(tmp_path, cfg, {'gemini_api_key': 'test-key',
        'google_application_credentials': 'unused.json', 'openai_api_key': 'test-openai'})
    monkeypatch.setattr(pipe, 'prompt', lambda *a: 'Return JSON')

    def post(url, **kw):
        assert url.endswith(':batchGenerateContent')
        assert kw['headers']['x-goog-api-key'] == 'test-key'
        assert 'Authorization' not in kw['headers']
        state.posts.append(kw['json'])
        return httpx.Response(200, json={'name': 'batches/one'})

    def get(url, **kw):
        state.gets.append(url)
        if state.pending: return httpx.Response(200, json={'state': 'JOB_STATE_RUNNING'})
        meta = state.posts[-1]['batch']['inputConfig']['requests']['requests'][0]['metadata']
        return httpx.Response(200, json={'state': 'JOB_STATE_SUCCEEDED', 'response': {
            'inlinedResponses': [{'metadata': meta, 'response': {'candidates': [
                {'finishReason': 'STOP', 'content': {'parts': [{'text': '{"ok":true}'}]}}]}}]}})

    monkeypatch.setattr(httpx, 'post', post)
    monkeypatch.setattr(httpx, 'get', get)

    def realtime(*args):
        state.realtime.append(args)
        return Completion(Result(ok=True), {}, 0, 'gpt-test')

    monkeypatch.setattr(pipe.providers['reviewer'], 'complete', realtime)
    return pipe, state


@pytest.mark.parametrize('stage', ['analyze', 'generate', 'revise'])
def test_each_gemini_stage_resumes_batch_without_realtime(tmp_path, calls, stage):
    pipe, state = calls
    with pytest.raises(ProviderError, match='batch_polling_timeout'):
        pipe.call(stage, {}, Result, tmp_path/'logs')
    state.pending = False
    assert pipe.call(stage, {}, Result, tmp_path/'logs').ok
    assert len(state.posts) == 1 and not state.realtime
    pipe.events = []
    assert pipe.call(stage, {}, Result, tmp_path/'logs').ok
    assert len(state.posts) == 1


def test_openai_review_stays_on_existing_transport(tmp_path, calls):
    pipe, state = calls
    assert pipe.call('review', {}, Result, tmp_path/'logs').ok
    assert len(state.realtime) == 1 and not state.posts


def test_changed_single_request_does_not_start_second_batch(tmp_path, calls):
    pipe, state = calls
    with pytest.raises(ProviderError): pipe.call('generate', {}, Result, tmp_path/'logs')
    with pytest.raises(ValueError, match='checkpoint_config_changed'):
        pipe.call('generate', {'changed': True}, Result, tmp_path/'logs')
    assert len(state.posts) == 1


def test_explicit_standard_mode_remains_available(tmp_path, calls, monkeypatch):
    pipe, state = calls
    pipe.config['pipeline']['mode'] = 'standard'
    monkeypatch.setattr(pipe.providers['generator'], 'complete',
        lambda *a: Completion(Result(ok=True), {}, 0, 'gemini-4-hydrogen'))
    assert pipe.call('generate', {}, Result, tmp_path/'logs').ok
    assert not state.posts


def test_existing_queue_client_accepts_both_inline_result_shapes():
    item = {'metadata': {'key': 'job'}, 'response': {}}
    client = batch.GeminiBatchClient('test-key')
    assert client.extract_items({'response': {'inlinedResponses': [item]}}) == [item]
    assert client.extract_items({'response': {'inlinedResponses': {'inlinedResponses': [item]}}}) == [item]


def test_default_config_is_gemini_batch():
    cfg = load_config(ROOT)
    assert cfg['pipeline']['mode'] == 'batch'
    assert cfg['models']['generator']['provider'] == 'gemini'
    assert cfg['models']['generator']['model'] == 'gemini-4-hydrogen'


def test_external_feedback_reuses_same_directory_after_pending(tmp_path, monkeypatch):
    from test_pipeline import setup_pipeline, review, flow_draft, SourceMeta, parse_feedback
    pipe, case, _, _, _ = setup_pipeline(tmp_path, [review('pass'), review('pass')], revisions=[flow_draft()])
    out = pipe.run(case['source_text'], SourceMeta.model_validate(case['metadata']), 'b'*64, tmp_path/'out')
    path = tmp_path/'out'/f"{out['content_id']}.json"
    before = path.read_bytes()
    issues = parse_feedback(ROOT/'examples/feedback.ko.json')
    original = pipe.review_loop
    seen = []

    def pending_once(*args, **kw):
        seen.append(args[5])
        if len(seen) == 1:
            raise ProviderError('gemini', 'gemini-4-hydrogen', 'batch_polling_timeout')
        return original(*args, **kw)

    monkeypatch.setattr(pipe, 'review_loop', pending_once)
    with pytest.raises(ProviderError): pipe.apply_feedback(path, issues, expected_version=out['version'])
    assert path.read_bytes() == before
    result = pipe.apply_feedback(path, issues, expected_version=out['version'])
    assert seen[0] == seen[1]
    assert seen[0].name == 'external-batch-feedback-1'
    assert result['generator']['feedback_revision'] == 1
