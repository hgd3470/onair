"""Collector-owned transport contract, with no real credentials or HTTP calls."""
import json

import httpx
import pytest

from issue_collector.core import Selection
from issue_collector.providers import APIProvider, ProviderError


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
def test_local_provider_sends_full_source_and_parses_structured_response(monkeypatch, provider):
    value = {'topics': [], 'coverage_notes': ['시험']}
    payload = {'articles': [{'text': '보존해야 하는 원문\n두 번째 문단.'}]}
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        if provider == 'gemini':
            response = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(value)}]}}]}
        else:
            response = {'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]}
        return httpx.Response(200, json=response)
    monkeypatch.setattr(httpx, 'post', post)
    model = APIProvider(dict(provider=provider, model='test-only', effort='medium', max_output_tokens=100),
                        {provider + '_api_key': 'test-key'})
    assert model.complete('시험', payload, Selection).value.model_dump() == value
    url, args = calls[0]
    request = args['json']
    sent = request['contents'][0]['parts'][0]['text'] if provider == 'gemini' else request['input']
    assert json.loads(sent) == payload
    assert 'test-key' not in url and 'test-key' not in json.dumps(request)


def test_local_provider_does_not_retry_ambiguous_timeout(monkeypatch):
    calls = []
    def post(*a, **kw):
        calls.append(1)
        raise httpx.ReadTimeout('never log this body')
    monkeypatch.setattr(httpx, 'post', post)
    provider = APIProvider(dict(provider='gemini',model='test-only',effort='medium',max_output_tokens=100), {'gemini_api_key': 'test-key'})
    with pytest.raises(ProviderError, match='transport_or_timeout'):
        provider.complete('시험', {}, Selection)
    assert len(calls) == 1
