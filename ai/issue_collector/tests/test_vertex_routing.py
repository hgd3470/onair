from datetime import date
from unittest.mock import MagicMock
import json
import httpx
import pytest
from issue_collector import llm, providers
from issue_collector.core import Selection


def test_credentials_preserve_vertex_settings_with_api_key(tmp_path, monkeypatch):
    keys={'gemini_api_key':'test-key','google_application_credentials':'test.json','gcp_project_id':'test-project','gcp_location':'global'}
    monkeypatch.setattr(providers,'credentials',lambda **kw:dict(keys))
    loaded=llm.credentials(tmp_path)
    assert all(loaded[k]==v for k,v in keys.items())


@pytest.mark.parametrize('provider', ['gemini','vertex'])
def test_explicit_provider_selects_auth_with_both_credentials(tmp_path,monkeypatch,provider):
    auth=MagicMock();auth.get_token.return_value='test-token';auth.project_id='test-project'
    monkeypatch.setattr(providers.APIProvider,'_get_auth_helper',lambda self:auth)
    calls=[]
    def post(url,**kwargs):
        calls.append((url,kwargs))
        return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps({'topics':[],'coverage_notes':[]})}]}}]})
    monkeypatch.setattr(httpx,'post',post)
    settings={'provider':provider,'model':'gemini-4-hydrogen','effort':'medium','max_output_tokens':100}
    keys={'gemini_api_key':'test-key','google_application_credentials':'test.json','gcp_project_id':'test-project','gcp_location':'global'}
    providers.APIProvider(settings,keys,root=tmp_path).complete('test',{},Selection)
    url,kwargs=calls[0]
    if provider=='vertex':
        assert url.startswith('https://aiplatform.googleapis.com/v1/projects/test-project/locations/global/')
        assert kwargs['headers']['Authorization']=='Bearer test-token'
        assert 'x-goog-api-key' not in kwargs['headers']
    else:
        assert url.startswith('https://generativelanguage.googleapis.com/')
        assert kwargs['headers']=={'x-goog-api-key':'test-key'}
        auth.get_token.assert_not_called()


@pytest.mark.parametrize('location', ['global','us-central1'])
def test_grounded_search_uses_vertex_and_keeps_secrets_out_of_logs(tmp_path,monkeypatch,location):
    auth=MagicMock();auth.get_token.return_value='test-token';auth.project_id='test-project'
    monkeypatch.setattr(providers.APIProvider,'_get_auth_helper',lambda self:auth)
    calls=[]
    def post(url,**kwargs):
        calls.append((url,kwargs))
        return httpx.Response(200,json={'modelVersion':'gemini-4-hydrogen','candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'Source https://example.org/news'}]},'groundingMetadata':{'groundingChunks':[{'web':{'uri':'https://example.org/news'}}]}}]})
    monkeypatch.setattr(httpx,'post',post)
    config={'models':{'generator':{'provider':'vertex','model':'gemini-4-hydrogen','effort':'medium'}},'collection':{'model_timeout_sec':10}}
    keys={'gemini_api_key':'test-key','google_application_credentials':'test.json','gcp_project_id':'test-project','gcp_location':location}
    models=llm.Models(tmp_path,config,keys,tmp_path/'logs')
    result=models.search('정책 발표',date(2026,11,12))
    assert result['urls']==['https://example.org/news']
    url,kwargs=calls[0]
    host='aiplatform.googleapis.com' if location=='global' else location+'-aiplatform.googleapis.com'
    assert url.startswith('https://'+host+'/v1/projects/test-project/locations/'+location+'/')
    assert kwargs['headers']['Authorization']=='Bearer test-token'
    assert kwargs['json']['tools']==[{'googleSearch':{}}]
    assert models.search('정책 발표',date(2026,11,12))==result
    assert len(calls)==1
    for p in (tmp_path/'logs').rglob('*.json'):
        assert 'test-token' not in p.read_text() and 'test-key' not in p.read_text()


def test_explicit_credential_root_resolves_relative_service_account(tmp_path):
    root=tmp_path/'collector'; root.mkdir()
    shared=tmp_path/'script_agent'; shared.mkdir()
    provider=providers.APIProvider({'provider':'vertex','credentials_root':'../script_agent'}, {'google_application_credentials':'service.json'},root=root)
    helper=provider._get_auth_helper()
    assert helper.root==shared.resolve()
    assert providers._resolve_sa_path(helper.raw_path,helper.root)==shared/'service.json'
