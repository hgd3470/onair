import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from pydantic import BaseModel
import pytest

from script_agent import providers
from script_agent.providers import APIProvider, ProviderError


class DummySchema(BaseModel):
    ok: bool


def test_credentials_picks_up_gcp_keys(tmp_path, monkeypatch):
    env_file = tmp_path / '.env'
    env_file.write_text(
        'openai_api_key=sk-test\n'
        'google_application_credentials=gcp-sa.json\n'
        'gcp_project_id=test-project\n'
        'gcp_location=asia-northeast3\n'
    )
    creds = providers.credentials(env_file, root=tmp_path)
    assert creds['openai_api_key'] == 'sk-test'
    assert creds['google_application_credentials'] == 'gcp-sa.json'
    assert creds['gcp_project_id'] == 'test-project'
    assert creds['gcp_location'] == 'asia-northeast3'


def test_api_provider_raises_missing_key_when_no_gemini_or_gcp():
    provider = APIProvider({'provider': 'gemini', 'model': 'gemini-3.8-flash', 'effort': 'medium', 'max_output_tokens': 100}, {})
    with pytest.raises(ProviderError) as exc_info:
        provider.complete('sys', {'test': 1}, DummySchema)
    assert exc_info.value.code == 'missing_key'


def test_api_provider_vertex_ai_request_format(tmp_path):
    settings = {
        'provider': 'gemini',
        'model': 'gemini-3.8-flash',
        'effort': 'medium',
        'max_output_tokens': 100,
    }
    keys = {
        'google_application_credentials': str(tmp_path / 'fake-key.json'),
        'gcp_project_id': 'my-sample-gcp-project',
        'gcp_location': 'asia-northeast3',
    }

    mock_auth = MagicMock()
    mock_auth.get_token.return_value = 'mock-oauth2-token'
    mock_auth.project_id = 'my-sample-gcp-project'

    captured = {}

    def mock_post(url, headers=None, json=None, timeout=None):
        captured['url'] = url
        captured['headers'] = headers
        captured['json'] = json
        resp = MagicMock()
        resp.is_error = False
        resp.status_code = 200
        resp.json.return_value = {
            'candidates': [{
                'content': {'parts': [{'text': '{"ok": true}'}]},
                'finishReason': 'STOP',
            }],
            'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 5, 'totalTokenCount': 15},
            'modelVersion': 'gemini-3.8-flash',
        }
        return resp

    provider = APIProvider(settings, keys, 30, root=tmp_path)
    provider._auth_helper = mock_auth

    with patch('httpx.post', side_effect=mock_post):
        result = provider.complete('system prompt', {'hello': 'world'}, DummySchema)

    assert result.value.ok is True
    assert captured['url'] == 'https://asia-northeast3-aiplatform.googleapis.com/v1/projects/my-sample-gcp-project/locations/asia-northeast3/publishers/google/models/gemini-3.8-flash:generateContent'
    assert captured['headers']['Authorization'] == 'Bearer mock-oauth2-token'
    assert 'responseSchema' in captured['json']['generationConfig']
    assert captured['json']['generationConfig']['thinkingConfig']['thinkingLevel'] == 'medium'
