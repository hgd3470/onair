import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import pytest
from issue_collector.core import Selection,Topic,Extraction,Verification,read,save
from issue_collector import pipeline
from issue_collector.acquisition import secure_url,parse_date
from issue_collector.llm import Models,CallFailure
from test_quality import sample

DAY=date(2026,11,11)

def test_script_agent_exact_input_contract(tmp_path):
    from script_agent.io import read_source
    e,v,articles=sample()
    for i,a in enumerate(articles):
        a.update(url=f'https://www.hani.co.kr/arti/{i}.html',title='공식 발표',publisher=f'편집국 {i}')
    facts=[f.model_dump() for f in e.facts]
    topic=Topic(id='policy',title='새 제도 발표',reason='공공 영향',importance=4,candidate_ids=['a','b'],search_query='query')
    path=pipeline.export_source(tmp_path,'issue',topic,facts,articles,DAY,60)
    text,meta,sha=read_source(path)
    assert meta.category=='C16' and meta.source_kind=='user_summary'
    assert len(meta.original_sources)==2 and meta.license.kind=='unknown'
    assert path.suffix=='.md'
    assert not path.with_suffix('.meta.json').exists()
    provenance=read(tmp_path.parent/'private/provenance/policy.json')
    assert len(provenance['original_sources'])==2
    assert all(s['license']['kind']=='unknown' for s in provenance['original_sources'])
    from issue_collector.core import digest
    assert provenance['source_file_sha256']==digest(path.read_text())
    assert provenance['source_body_sha256']==digest(text)
    assert e.facts[0].text in text
    assert all(a['text'] in text for a in articles)

@pytest.mark.parametrize('url',[
    'file:///etc/passwd','http://localhost/a','https://www.hani.co.kr@127.0.0.1/a',
    'https://evil.invalid/a','https://www.hani.co.kr:8080/a'])
def test_unsafe_discovery_urls_rejected(url):
    with pytest.raises(ValueError): secure_url(url)

def test_private_dns_answer_rejected(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo',lambda *a:[(None,None,None,None,('127.0.0.1',443))])
    with pytest.raises(ValueError,match='nonpublic'): secure_url('https://www.hani.co.kr/a')

def test_incomplete_call_not_repeated(tmp_path):
    from issue_collector.core import digest
    root=tmp_path/'root';(root/'prompts').mkdir(parents=True)
    (root/'prompts/select.txt').write_text('prompt')
    cfg={'models':{'generator':{'provider':'gemini'}},'collection':{'model_timeout_sec':10}}
    model=Models(root,cfg,{},tmp_path/'logs')
    request={'stage':'select','prompt':'prompt','payload':{},'settings':cfg['models']['generator'],'schema':Selection.model_json_schema()}
    folder=tmp_path/'logs'/('select-'+digest(request)[:20]);save(folder/'request.json',request)
    with pytest.raises(CallFailure,match='ambiguous'): model.call('select',{},Selection)

def test_completed_call_reused_without_provider(tmp_path,monkeypatch):
    import issue_collector.llm as llm
    root=tmp_path/'root';(root/'prompts').mkdir(parents=True);(root/'prompts/select.txt').write_text('prompt')
    cfg={'models':{'generator':{}},'collection':{'model_timeout_sec':10}}
    value=Selection(topics=[],coverage_notes=['no evidence'])
    counter=[]
    class Provider:
        def __init__(self,*a,**kw): pass
        def complete(self,*a):
            counter.append(1)
            return SimpleNamespace(value=value,response_model='test',usage={},elapsed_sec=0)
    monkeypatch.setattr(llm,'APIProvider',Provider)
    model=Models(root,cfg,{},tmp_path/'logs')
    assert model.call('select',{},Selection)==value
    assert model.call('select',{},Selection)==value
    assert len(counter)==1


def test_partial_run_does_not_replace_latest_good(tmp_path,monkeypatch):
    cfg={'collection':{'stream_workers':2}}
    base=tmp_path/'output/20261111'
    save(base/'latest.json',{'run_id':'good','path':'runs/good'})
    monkeypatch.setattr(pipeline,'credentials',lambda r:{})
    monkeypatch.setattr(pipeline,'collect_stream',lambda *a:{'status':'partial','items':[]})
    result,_=pipeline.run(tmp_path,cfg,DAY,['issue','market'],3,'failed')
    assert result['status']=='partial'
    assert read(base/'latest.json')['run_id']=='good'
    assert read(base/'latest-attempt.json')['run_id']=='failed'


def test_changed_contract_cannot_resume(tmp_path,monkeypatch):
    cfg={'collection':{'stream_workers':1}}
    monkeypatch.setattr(pipeline,'credentials',lambda r:{})
    monkeypatch.setattr(pipeline,'collect_stream',lambda *a:{'status':'partial','items':[]})
    pipeline.run(tmp_path,cfg,DAY,['issue'],3,'same')
    with pytest.raises(ValueError,match='contract_changed'):
        pipeline.run(tmp_path,cfg,DAY,['issue'],4,'same',resume=True)

@pytest.mark.parametrize('central_verified,expected',[(True,'complete'),(False,'partial')])
def test_collector_handoff_requires_core_event(tmp_path,monkeypatch,central_verified,expected):
    e,v,articles=sample()
    original=e.facts[0]
    e.facts=[original.model_copy(update={'id':f'F{i}','text':f'검증된 개별 사실 {i}입니다.'}) for i in range(1,4)]
    original_check=v.checks[0]
    v.checks=[original_check.model_copy(update={'fact_id':f'F{i}'}) for i in range(1,4)]
    v.topic_confirmed=central_verified
    v.central_fact_ids=['F1'] if central_verified else []
    for i,a in enumerate(articles):
        a.update(url=f'https://www.hani.co.kr/arti/{i}.html',title='발표',publisher=f'편집국 {i}')
    topic=Topic(id='policy',title='공식 발표',reason='영향',importance=4,candidate_ids=['a','b'],search_query='q')
    class FakeModels:
        def __init__(self,*args): pass
        def call(self,stage,payload,schema):
            return {'select':Selection(topics=[topic],coverage_notes=[]),'extract':e,'verify':v}[stage]
    monkeypatch.setattr(pipeline,'Models',FakeModels)
    monkeypatch.setattr(pipeline,'discover',lambda *a:articles)
    monkeypatch.setattr(pipeline,'acquire',lambda fetcher,item,*a:item)
    cfg={'collection':{'http_timeout_sec':1,'candidate_topics':1,'max_articles_per_topic':8,
        'http_workers':2,'min_independent_sources':2,'google_search':False,'min_facts':3},'script':{'target_sec':60}}
    result=pipeline.collect_stream(tmp_path,cfg,{},DAY,'issue',tmp_path/'run',1)
    assert result['status']==expected
    if central_verified:
        card=read(tmp_path/'run/cards/policy.json')
        assert card['license']=='All rights reserved'
        assert '© 틈(TEUM) 팀' in card['copyright']
        assert card['rights_status']=='unverified'
        assert '© 틈(TEUM) 팀' in (tmp_path/'run/summary.txt').read_text()
    assert len(list((tmp_path/'run/sources').glob('*.md')))==(1 if central_verified else 0)


def test_export_delivers_attribution_as_actual_source_text(tmp_path):
    from script_agent.io import read_source
    from script_agent.news_context import parse_facts
    e,v,articles=sample()
    for i,a in enumerate(articles):
        a.update(url=f'https://www.hani.co.kr/arti/{i}.html',title='공식 발표',publisher=f'편집국 {i}')
    facts=[f.model_dump() for f in e.facts]
    facts[0]['attribution']='한국거래소'
    topic=Topic(id='market',title='시장 브리핑',reason='영향',importance=4,candidate_ids=['a','b'],search_query='q')
    path=pipeline.export_source(tmp_path,'market',topic,facts,articles,DAY,60)
    text,meta,sha=read_source(path)
    rows=parse_facts(text)
    assert rows[0]['attribution']=='한국거래소'
    assert rows[0]['text']==facts[0]['text']
    assert rows[0]['date']=='2026-11-11'
    assert '한국거래소' in text
    assert meta.license.kind=='unknown' and meta.category=='C08'


def test_changed_shared_handoff_code_cannot_resume(tmp_path,monkeypatch):
    cfg={'collection':{'stream_workers':1}}
    monkeypatch.setattr(pipeline,'credentials',lambda r:{})
    monkeypatch.setattr(pipeline,'collect_stream',lambda *a:{'status':'partial','items':[]})
    module=tmp_path/'shared_handoff.py';module.write_text('version one')
    monkeypatch.setattr(pipeline.handoff,'__file__',str(module))
    pipeline.run(tmp_path,cfg,DAY,['issue'],3,'same')
    module.write_text('version two')
    with pytest.raises(ValueError,match='contract_changed'):
        pipeline.run(tmp_path,cfg,DAY,['issue'],3,'same',resume=True)


def test_export_keeps_media_internal_and_does_not_label_authority_as_publisher(tmp_path):
    from script_agent.io import read_source
    from script_agent.news_context import MEDIA_PREFIX
    e,v,articles=sample()
    for i,a in enumerate(articles):
        a.update(url=f'https://www.hani.co.kr/arti/{i}.html',title='발표',publisher='한국거래소' if i==0 else '연합뉴스',official=i==0)
    topic=Topic(id='market',title='시장 동향',reason='공공 영향',importance=4,candidate_ids=['a','b'],search_query='q')
    path=pipeline.export_source(tmp_path,'market',topic,[f.model_dump() for f in e.facts],articles,DAY,60)
    text,meta,_=read_source(path)
    import json
    media=json.loads(next(line[len(MEDIA_PREFIX):] for line in text.splitlines() if line.startswith(MEDIA_PREFIX)))
    assert media==['연합뉴스']
    assert {x.author for x in meta.original_sources}=={'한국거래소','연합뉴스'}
    provenance=read(tmp_path.parent/'private/provenance/market.json')
    assert {x['author'] for x in provenance['original_sources']}=={'한국거래소','연합뉴스'}


def test_shared_editorial_policy_avoids_old_source_credit_instruction():
    from issue_collector.editorial import news_brief,audience_policy
    assert audience_policy()['audience']=='40~50대 직장인'
    brief=' '.join(news_brief(DAY,'market'))
    assert '출처는 매체 이름을 짧게 밝혀라' not in brief
    assert '첫 문장은 검증된 핵심 사건' in brief
    assert '합니다/했습니다' in brief
    assert '고용동향을 가계대출 동향으로 바꾸지 않는다' in brief
