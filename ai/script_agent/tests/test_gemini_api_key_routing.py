"""Gemini API-key mode must survive unrelated host service-account settings."""
from unittest.mock import MagicMock
from pydantic import BaseModel
import httpx
import pytest
from script_agent.providers import APIProvider


class Result(BaseModel):
    ok: bool


@pytest.mark.parametrize('provider',['gemini','vertex'])
def test_explicit_provider_with_both_credentials(provider,monkeypatch):
    settings={'provider':provider,'model':'gemini-3.8-flash','effort':'medium','max_output_tokens':100}
    keys={'gemini_api_key':'test-key','google_application_credentials':'unused.json','gcp_project_id':'test-project'}
    api=APIProvider(settings,keys,30)
    auth=MagicMock();auth.get_token.return_value='test-token';auth.project_id='test-project'
    helper=MagicMock(return_value=auth);monkeypatch.setattr(api,'_get_auth_helper',helper)
    calls=[]
    def post(url,**kw):
        calls.append((url,kw['headers']))
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'{"ok":true}'}]},'finishReason':'STOP'}],
            'usageMetadata':{},'modelVersion':'gemini-3.8-flash'})
    monkeypatch.setattr(httpx,'post',post)
    assert api.complete('Return JSON',{},Result).value.ok
    assert len(calls)==1
    url,headers=calls[0]
    if provider=='gemini':
        assert url.startswith('https://generativelanguage.googleapis.com/')
        assert headers=={'x-goog-api-key':'test-key'}
        helper.assert_not_called()
    else:
        assert '-aiplatform.googleapis.com/' in url
        assert headers['Authorization']=='Bearer test-token'
        assert 'x-goog-api-key' not in headers
        helper.assert_called_once()
