"""Offline regression for optional opinions, source boundary and downstream credits."""
import copy
import json
from datetime import date
from pathlib import Path

import pytest

from issue_collector.core import Opinion, OpinionCheck, Topic
from issue_collector.handoff import export_source
from issue_collector.quality import validate_facts, validate_opinions
from script_agent.io import read_source
from script_agent.models import Draft, Material
from script_agent.news_context import news_checks
from script_agent.news_opinions import parse_opinions, apply_news_attribution
from test_quality import sample

DAY = date(2026, 11, 11)


def case():
    extraction, verification, articles = sample()
    for i, a in enumerate(articles):
        a.update(publisher=['가일보', '나방송'][i], url=f'https://example.org/{i}', title='제도 발표')
        a['text'] += ' 금리 기대 약화가 부담이라는 분석이다.'
    extraction.opinions = [Opinion(id=f'O{i+1}', article_id=a['id'], attribution=a['publisher'],
        speaker='', quote='금리 기대 약화가 부담이라는 분석이다.', text='금리 기대 약화를 부담으로 분석했다.',
        mode='indirect', related_fact_ids=['F1'], reason='확인된 제도 변화에 대한 해석') for i, a in enumerate(articles)]
    verification.opinion_checks = [OpinionCheck(opinion_id=o.id, verdict='approved', attribution_correct=True,
        context_preserved=True, relevant=True, reason='기사 자체 분석 확인') for o in extraction.opinions]
    return extraction, verification, articles


def validated():
    e, v, a = case()
    facts, _ = validate_facts(e, v, a, DAY)
    opinions, rejected = validate_opinions(e, v, a, facts)
    assert len(opinions) == 2 and not rejected
    return e, v, a, facts, opinions


def make_source(tmp_path, *, direct=False, speaker='', opinions=True, second_context=''):
    e, v, a, facts, selected = validated()
    a[1]['text'] = second_context + a[1]['text']
    if direct:
        selected[0].update(mode='direct', text=selected[0]['quote'])
    selected[0]['speaker'] = speaker
    if speaker:
        a[0]['text'] += f' {speaker}의 분석을 전했다.'
    topic = Topic(id='policy', title='제도 변경', reason='시험', importance=3, candidate_ids=['a','b'], search_query='제도')
    path = export_source(tmp_path, 'issue', topic, facts, a, DAY, 60, opinions=selected if opinions else [])
    text, meta, _ = read_source(path)
    return path, text, meta, selected, a


def draft_material(opinions, *, both=False, direct=False, speaker=''):
    material = Material(production_type='사실해설형', category='C16', routing_reason='시험', summary='제도 변경',
        conflict='', details=[], question='변화', unknowns=[], outline=[], facts=[
            dict(id=f'f{i+1}', text=o['text'], quote=o['quote'], kind='opinion', conditions=[o['id']])
            for i, o in enumerate(opinions)])
    line = ('가일보는 “금리 기대 약화가 부담이라는 분석이다.”라고 전했습니다.' if direct else
            ('가일보와 나방송은 금리 기대 약화를 부담으로 꼽았습니다.' if both else
             f'가일보는 {speaker+"의 " if speaker else ""}금리 기대 약화 분석을 전했습니다.'))
    draft = Draft(title='제도 변경 발표', format='정보형', tags=[],
        script=['정부가 제도 변경을 발표했습니다.', line],
        claims=[dict(sentence=1, kind='source_statement', fact_ids=[]),
                dict(sentence=2, kind='quotation' if direct else 'opinion', fact_ids=['f1','f2'] if both else ['f1'])],
        adaptation_note={'from_source':[], 'new':[]}, unresolved=[], changes=[])
    return draft, material


def output(draft, material, text, meta):
    return {**{k:v for k,v in draft.model_dump().items() if k != 'changes'},
            'material':material.model_dump(), 'sources':[{'text':text}], 'attribution':'stale',
            'input_metadata':meta.model_dump(), 'production_type':'사실해설형', 'input_sha256':'a'*64,
            'self_review':[{'verdict':'pass'}], 'status':'hold', 'rights_status':'unverified'}


def test_optional_single_source_opinions_do_not_change_fact_threshold():
    e,v,a,facts,opinions = validated()
    assert len(facts) == 1  # opinions are not extra facts or independent corroboration
    e.facts[0].kind='opinion'
    assert not validate_facts(e,v,a,DAY)[0]
    assert not validate_opinions(e,v,a,[])[0]
    e.opinions=[];v.opinion_checks=[]
    assert validate_opinions(e,v,a,facts)==([],[])


@pytest.mark.parametrize('change,reason',[
    ('fake_quote','opinion_quote_not_in_article'),('speaker','opinion_speaker_not_in_article'),('publisher','opinion_publisher_mismatch'),
    ('official','opinion_publisher_mismatch'),('missing_check','missing_or_duplicate_opinion_check'),
    ('duplicate_check','missing_or_duplicate_opinion_check'),('bad_context','opinion_not_approved'),
    ('irrelevant','opinion_not_approved'),('rejected','opinion_not_approved'),
    ('bad_fact','opinion_without_verified_context'),('direct_changed','direct_quote_changed'),
    ('duplicate_id','duplicate_opinion_id'),('duplicate_quote','duplicate_opinion_quote')])
def test_invalid_opinions_are_isolated(change,reason):
    e,v,a,facts,_=validated()
    if change=='fake_quote':e.opinions[0].quote='존재하지 않는 원문입니다.'
    if change=='speaker':e.opinions[0].speaker='없는전문가'
    if change=='publisher':e.opinions[0].attribution='나방송 / https://example.org'
    if change=='official':a[0]['official']=True
    if change=='missing_check':v.opinion_checks=v.opinion_checks[1:]
    if change=='duplicate_check':v.opinion_checks.append(v.opinion_checks[0])
    if change=='bad_context':v.opinion_checks[0].context_preserved=False
    if change=='irrelevant':v.opinion_checks[0].relevant=False
    if change=='rejected':v.opinion_checks[0].verdict='reject'
    if change=='bad_fact':e.opinions[0].related_fact_ids=['F99']
    if change=='direct_changed':e.opinions[0].mode='direct'
    if change=='duplicate_id':e.opinions[1].id=e.opinions[0].id
    if change=='duplicate_quote':e.opinions[1].article_id=e.opinions[0].article_id
    accepted,rejected=validate_opinions(e,v,a,facts)
    assert any(reason in o['reasons'] for o in rejected)
    assert validate_facts(e,v,a,DAY)[0]==facts


def test_handoff_carries_only_selected_publisher_names_and_full_provenance(tmp_path):
    path,text,meta,opinions,articles=make_source(tmp_path)
    assert meta.attribution=='가일보, 나방송'
    assert parse_opinions(text)==[{k:v for k,v in o.items() if k!='verification'} for o in opinions]
    assert all(a['text'] in text for a in articles)
    assert {s.source_url for s in meta.original_sources}=={a['url'] for a in articles}
    assert '귀속 주체: 정부' in text
    path,text,meta,_,_=make_source(tmp_path/'empty',opinions=False)
    assert meta.attribution=='' and parse_opinions(text)==[]


@pytest.mark.parametrize('change',['quote','publisher','fact','missing_list','duplicate_list','official','metadata'])
def test_tampered_standalone_input_rejected(tmp_path,change):
    path,text,meta,opinions,_=make_source(tmp_path)
    raw=path.read_text()
    if change in {'quote','publisher','fact'}:
        key={'quote':'quote','publisher':'attribution','fact':'related_fact_ids'}[change]
        rows=parse_opinions(text);rows[0][key]={'quote':'위조된 짧은 문장입니다.','publisher':'가짜일보','fact':['F99']}[change]
        raw=raw.replace(next(l for l in text.splitlines() if l.startswith('의견 인용 목록: ')), '의견 인용 목록: '+json.dumps(rows,ensure_ascii=False))
    if change=='missing_list':raw=raw.replace('의견 인용 목록: ','다른 목록: ')
    if change=='duplicate_list':raw=raw.replace('의견 인용 목록: ','의견 인용 목록: []\n의견 인용 목록: ')
    if change=='official':raw=raw.replace('"official": false','"official": true')
    if change=='metadata':raw=raw.replace('attribution: 가일보, 나방송','attribution: 엉뚱일보')
    path.write_text(raw)
    with pytest.raises(ValueError):read_source(path)


@pytest.mark.parametrize('both,expected',[(False,'가일보'),(True,'가일보, 나방송')])
def test_only_used_opinions_are_credited_even_when_quotes_are_identical(tmp_path,both,expected):
    _,text,meta,opinions,_=make_source(tmp_path)
    draft,material=draft_material(opinions,both=both)
    assert news_checks(draft,material,text)==[]
    out=output(draft,material,text,meta)
    apply_news_attribution(out)
    assert out['attribution']==expected
    # Removing the opinion on an external revision removes its credits too.
    draft.script=draft.script[:1];draft.claims=draft.claims[:1]
    out=output(draft,material,text,meta);apply_news_attribution(out)
    assert out['attribution']==''


@pytest.mark.parametrize('change',['fact_kind','card_kind','no_link','wrong_publisher','extra_publisher','title','opener','changed_quote','quote_as_indirect'])
def test_invalid_narration_enters_revision_checks(tmp_path,change):
    direct=change in {'changed_quote','quote_as_indirect'}
    _,text,meta,opinions,_=make_source(tmp_path,direct=direct)
    draft,material=draft_material(opinions,direct=direct)
    if change=='fact_kind':draft.claims[1].kind='fact'
    if change=='card_kind':material.facts[0].kind='source_statement'
    if change=='no_link':draft.claims[1].fact_ids=[]
    if change=='wrong_publisher':draft.script[1]=draft.script[1].replace('가일보','나방송')
    if change=='extra_publisher':draft.script[1]=draft.script[1].replace('가일보는','가일보와 나방송은')
    if change=='title':draft.title='가일보의 제도 분석'
    if change=='opener':draft.script.reverse();draft.claims[1].sentence=1;draft.claims[0].sentence=2
    if change=='changed_quote':draft.script[1]=draft.script[1].replace('부담','호재')
    if change=='quote_as_indirect':draft.claims[1].kind='opinion'
    assert news_checks(draft,material,text)


def test_direct_quote_and_expert_context(tmp_path):
    _,text,meta,opinions,_=make_source(tmp_path,direct=True)
    draft,material=draft_material(opinions,direct=True)
    assert news_checks(draft,material,text)==[]
    _,text,meta,opinions,_=make_source(tmp_path/'speaker',speaker='김연구원')
    draft,material=draft_material(opinions,speaker='김연구원')
    assert news_checks(draft,material,text)==[]
    draft.script[1]=draft.script[1].replace('김연구원의 ','')
    assert news_checks(draft,material,text)


def test_opinions_require_new_content_assessment_not_facts_only_label(tmp_path):
    from script_agent.use_assessment import UseAssessment, ContentReview, SourceProcessing, fingerprint, apply_use_assessment
    _,text,meta,opinions,articles=make_source(tmp_path)
    draft,material=draft_material(opinions)
    out=apply_news_attribution(output(draft,material,text,meta))
    review=ContentReview(verdict='pass',facts_only=False,attributed_opinions=True,independent_wording=True,
        no_distinctive_expression=True,covered_sentences=[1,2],findings=[],rationale='의견 귀속 확인')
    out['use_assessment']=UseAssessment(policy_version='news-attribution-v2',basis='facts_with_optional_attributed_opinions',
        content_sha256=fingerprint(out),evidence_sha256='b'*64,evidence_report='test',reviewed_at='2026-11-12T00:00:00+09:00',
        reviewer='offline',evidence_verified=True,content=review,
        processing=[SourceProcessing(source_url=a['url'],article_sha256='c'*64) for a in articles]).model_dump()
    apply_use_assessment(out)
    assert out['status']=='ready_for_downstream_review'
    assert out['rights_status']=='attributed_news_reviewed_not_legal_approval'
    assert out['release_status']=='pending_processing_review'
    for field,value in [('facts_only',True),('attributed_opinions',False)]:
        invalid=copy.deepcopy(out);invalid['use_assessment']['content'][field]=value
        apply_use_assessment(invalid);assert invalid['status']=='hold'
    invalid=copy.deepcopy(out);invalid['attribution']='가일보, 나방송'
    apply_use_assessment(invalid);assert invalid['status']=='hold'


@pytest.mark.parametrize('mode', ['standard', 'batch'])
def test_generation_feedback_and_publication_keep_actual_credits(tmp_path, monkeypatch, mode):
    import shutil
    import httpx
    from script_agent.io import load_config
    from script_agent.pipeline import Pipeline
    from script_agent.providers import Completion, APIProvider
    from script_agent.models import Review, RevisionResult, Issue, Handoff
    from issue_collector.publication import apply_output_license
    monkeypatch.setattr(httpx, 'post', lambda *a, **kw: pytest.fail('unexpected network'))
    source,text,meta,opinions,articles=make_source(tmp_path/'collected')
    _,_,sha=read_source(source)
    draft,material=draft_material(opinions)
    from script_agent.models import Fact
    material.facts.insert(0, Fact(id='f3', text='정부 제도 발표', quote=sample()[0].facts[0].evidence[0].quote,
                                kind='source_statement', conditions=['F1']))
    draft.claims[0].fact_ids=['f3']
    # Keep length checks meaningful without requiring a 60-second mocked narrative.
    root=tmp_path/'agent'
    agent=Path(__file__).resolve().parents[2]/'script_agent'
    shutil.copytree(agent/'prompts',root/'prompts')
    cfg=load_config(agent)
    cfg['pipeline'].update(chars_per_sec=len(''.join(draft.script))/60,max_revisions=1)
    review=Review(verdict='pass',issues=[],scores=dict(grounding=4,listening=4,engagement=4,type_fit=4),strengths=[],remaining=[])
    state={'draft':draft}
    class Fake:
        def complete(self, system, payload, schema):
            value={'Material':material,'Draft':state['draft'],'Review':review,
                   'RevisionResult':RevisionResult(material=None,draft=state['draft'])}[schema.__name__]
            return Completion(value.model_copy(deep=True),{},0,'offline')
    pipe=Pipeline(root,cfg,{},providers={'generator':Fake(),'reviewer':Fake()})
    if mode=='standard':
        out=pipe.run(text,meta,sha,root/'generated',source_file=source)
        path=root/'generated'/f'S2-{sha[:16]}.json'
    else:
        from script_agent.batch import GeminiBatchClient
        from script_agent.queue import process_queue
        pending=root/'input';pending.mkdir();shutil.copy2(source,pending/source.name)
        requests={}
        def create(self, model, jobs, display_name):
            key=str(len(requests));requests[key]=jobs;return key
        def poll(self,key,**kwargs):
            rows=[]
            for req in requests[key]:
                payload=json.loads(req['request']['contents'][0]['parts'][0]['text'])
                value=state['draft'] if 'material' in payload else material
                rows.append({'metadata':req['metadata'],'response':{'candidates':[{'finishReason':'STOP',
                    'content':{'parts':[{'text':value.model_dump_json()}]}}]}})
            return {'state':'BATCH_STATE_SUCCEEDED','response':{'inlinedResponses':{'inlinedResponses':rows}}}
        monkeypatch.setattr(GeminiBatchClient,'create_batch',create)
        monkeypatch.setattr(GeminiBatchClient,'poll_batch',poll)
        pipe.providers['generator']=APIProvider(cfg['models']['generator'],{'gemini_api_key':'offline-test'})
        assert process_queue(root,pipe,mode='batch')[0]['status']=='completed'
        path=next((root/'output').rglob('S2-*.json'));out=json.loads(path.read_text())
        pipe.providers['generator']=Fake()
    assert out['attribution']=='가일보'
    assert out['input_metadata']['attribution']=='가일보, 나방송'
    assert apply_output_license(out)['attribution']=='가일보'
    Handoff.model_validate(out)
    state['draft']=draft.model_copy(deep=True)
    state['draft'].script=state['draft'].script[:1];state['draft'].claims=state['draft'].claims[:1]
    updated=pipe.apply_feedback(path,[Issue(sentence=2,problem='인용 생략',reason='선택적 의견 삭제',direction='인용 생략')],out['version'])
    assert updated['attribution']==''
    assert updated['generator']['revision']==out['generator']['revision']
    assert updated['generator']['feedback_revision']==1
    assert apply_output_license(updated)['attribution']==''


@pytest.mark.parametrize('rejected', [False, True])
def test_collect_keeps_fact_gate_and_exports_only_approved_opinions(tmp_path,monkeypatch,rejected):
    from issue_collector import pipeline
    from issue_collector.core import Selection
    e,v,articles=case()
    e.facts=[e.facts[0].model_copy(update={'id':f'F{i}', 'text':f'확인된 제도 변경 사항 {i}입니다.'}) for i in range(1,4)]
    v.checks=[v.checks[0].model_copy(update={'fact_id':f'F{i}'}) for i in range(1,4)]
    v.topic_confirmed=True;v.central_fact_ids=['F1']
    if rejected:
        for check in v.opinion_checks:check.context_preserved=False
    topic=Topic(id='policy',title='제도 변경',reason='영향',importance=3,candidate_ids=['a','b'],search_query='제도')
    class FakeModels:
        def __init__(self,*a):pass
        def call(self,stage,*a):return {'select':Selection(topics=[topic],coverage_notes=[]),'extract':e,'verify':v}[stage]
    monkeypatch.setattr(pipeline,'Models',FakeModels)
    monkeypatch.setattr(pipeline,'discover',lambda *a:articles)
    monkeypatch.setattr(pipeline,'acquire',lambda fetcher,item,*a:item)
    cfg={'collection':{'http_timeout_sec':1,'candidate_topics':1,'max_articles_per_topic':8,'http_workers':2,
         'min_independent_sources':2,'google_search':False,'min_facts':3},'script':{'target_sec':60}}
    result=pipeline.collect_stream(tmp_path,cfg,{},DAY,'issue',tmp_path/'run',1)
    assert result['status']=='complete'
    card=result['items'][0]
    assert len(card['facts'])==3 and len(card['opinions'])==(0 if rejected else 2)
    assert card['attribution']==('' if rejected else '가일보, 나방송')
    text,meta,_=read_source(tmp_path/'run'/card['source_file'])
    assert meta.attribution==card['attribution']
    assert len(parse_opinions(text))==len(card['opinions'])


def test_broadcast_alias_and_overlapping_publisher_name(tmp_path):
    e,v,articles,facts,opinions=validated()
    articles[0]['publisher']='연합뉴스TV';opinions[0]['attribution']='연합뉴스TV'
    articles[1]['publisher']='연합뉴스';opinions[1]['attribution']='연합뉴스'
    topic=Topic(id='policy',title='제도 변경',reason='시험',importance=3,candidate_ids=['a','b'],search_query='제도')
    path=export_source(tmp_path,'issue',topic,facts,articles,DAY,60,opinions=opinions)
    text,meta,_=read_source(path)
    draft,material=draft_material(opinions)
    draft.script[1]='연합뉴스티브이는 금리 기대 약화를 부담으로 꼽았습니다.'
    assert not news_checks(draft,material,text)
    assert apply_news_attribution(output(draft,material,text,meta))['attribution']=='연합뉴스TV'
    draft.script[1]='연합뉴스티브이와 연합뉴스는 금리 기대 약화를 꼽았습니다.'
    assert news_checks(draft,material,text)


def test_expanded_quote_must_stay_in_approved_article(tmp_path):
    from script_agent.news_opinions import opinion_links
    _,text,_,opinions,articles=make_source(tmp_path)
    draft,material=draft_material(opinions)
    material.facts[0].quote=articles[0]['text']
    assert opinion_links(draft,material,text)[2][0]['id']=='O1'
    material.facts[0].quote='없는 발언자 설명 ' + opinions[0]['quote']
    assert not opinion_links(draft,material,text)
    material.facts[0].quote=opinions[0]['quote'][:-2]
    assert not opinion_links(draft,material,text)
    material.facts[0].quote=articles[0]['text']
    material.facts[0].conditions=[]
    assert not opinion_links(draft,material,text)


def test_expansion_from_another_article_cannot_supply_context(tmp_path):
    from script_agent.news_opinions import opinion_links
    _,text,_,opinions,articles=make_source(tmp_path,second_context='다른 기사에만 있는 문맥. ')
    draft,material=draft_material(opinions)
    material.facts[0].quote=articles[1]['text']
    assert articles[1]['text'] not in articles[0]['text']
    assert not opinion_links(draft,material,text)


@pytest.mark.parametrize('speaker,display',[
    ('김지연 KDI 경제전망실 전망총괄','김지연 총괄'),
    ('윤영중 국토교통부 주택정비정책관','윤영중 정책관'),
    ('곽범준 금융감독원 은행담당 부원장보','곽범준 부원장보'),
    ('한은 관계자','한은 관계자'),
])
def test_shortened_role_and_non_whitelisted_report_verb(tmp_path,speaker,display):
    _,text,meta,opinions,_=make_source(tmp_path,speaker=speaker)
    draft,material=draft_material(opinions,speaker=display)
    draft.script[1]=f'가일보에서 {display}는 금리 기대 약화를 지목했습니다.'
    assert not news_checks(draft,material,text)
    assert apply_news_attribution(output(draft,material,text,meta))['attribution']=='가일보'
    draft.script[1]='가일보에서 다른 사람은 금리 기대 약화를 지목했습니다.'
    problems=news_checks(draft,material,text)
    assert any(x.problem=='의견 인용 근거·귀속 오류' for x in problems)
    assert not any(x.problem=='대본에 내부 매체 출처 노출' for x in problems)


def test_anonymous_speaker_cannot_drop_institution_or_role():
    from script_agent.news_opinions import speaker_in_line
    for line in ['한은은 우려를 지목했습니다.','관계자는 우려를 지목했습니다.']:
        assert not speaker_in_line('한은 관계자',line)
    assert not speaker_in_line('김지연 KDI 전망총괄','박김지연 총괄은 전망했습니다.')


def test_adjacent_opinion_can_share_credit_but_not_across_fact_or_other_opinion(tmp_path):
    _,text,meta,opinions,_=make_source(tmp_path,speaker='한은 관계자')
    draft,material=draft_material(opinions,speaker='한은 관계자')
    draft.script.append('그는 금리 기대 약화를 부담으로 지목했습니다.')
    draft.claims.append(draft.claims[1].model_copy(update={'sentence':3}))
    assert not news_checks(draft,material,text)
    assert apply_news_attribution(output(draft,material,text,meta))['attribution']=='가일보'
    draft.claims[2].fact_ids=['f2']
    assert news_checks(draft,material,text)
    draft.claims[2].fact_ids=['f1']
    draft.script.insert(2,'정부는 제도를 발표했습니다.')
    draft.claims[2].sentence=4
    draft.claims.append(draft.claims[0].model_copy(update={'sentence':3}))
    assert news_checks(draft,material,text)


def test_optional_opinion_absence_is_valid_and_empty_credit(tmp_path):
    _,text,meta,opinions,_=make_source(tmp_path)
    draft,material=draft_material(opinions)
    draft.script=draft.script[:1];draft.claims=draft.claims[:1]
    assert not news_checks(draft,material,text)
    assert apply_news_attribution(output(draft,material,text,meta))['attribution']==''


def test_semantic_review_gets_explicit_adjacent_attribution_context(tmp_path):
    from issue_collector.use_review import opinion_review_context
    _,text,meta,opinions,_=make_source(tmp_path,speaker='한은 관계자')
    draft,material=draft_material(opinions,speaker='한은 관계자')
    draft.script.append('투자심리에 부담이라는 분석입니다.')
    draft.claims.append(draft.claims[1].model_copy(update={'sentence':3}))
    context=opinion_review_context(output(draft,material,text,meta))
    assert context[1]['sentence']==3
    assert context[1]['explicit_attribution_sentences']==[2]
    assert [x['sentence'] for x in context[1]['scope']]==[2,3]
    draft.claims[2].fact_ids=['f2']
    assert opinion_review_context(output(draft,material,text,meta))[1]['explicit_attribution_sentences']==[]


@pytest.mark.parametrize('handoff_version', [5, 6])
def test_fact_reporting_can_name_the_linked_newspaper_without_opinion(tmp_path, handoff_version):
    path, text, meta, _, articles = make_source(tmp_path, opinions=False)
    if handoff_version == 5:
        text = text.replace('원문 인계 v6\n', '원문 인계 v5\n', 1)
        text = text.replace('검증용 보도 매체: ', '내부 검증용 보도 매체(낭독 금지): ', 1)
    from script_agent.news_context import parse_facts
    evidence = json.loads(next(line[len('근거 F1: '):] for line in text.splitlines() if line.startswith('근거 F1: ')))
    draft, material = draft_material([])
    from script_agent.models import Fact, Claim
    material.facts = [Fact(id='f1', text='제도 변경 발표', quote=evidence[0]['quote'], kind='source_statement', conditions=['F1'])]
    draft.script = ['가일보에 따르면 정부가 제도 변경을 발표했습니다.']
    draft.claims = [Claim(sentence=1, kind='fact', fact_ids=['f1'])]
    assert not [i for i in news_checks(draft, material, text) if '매체' in i.problem or '인용' in i.problem]
    out = apply_news_attribution(output(draft, material, text, meta))
    assert out['attribution'] == '가일보'
    # Keeping the newspaper name without the actual card link is not accepted.
    draft.claims[0].fact_ids = []
    assert any('매체' in i.problem for i in news_checks(draft, material, text))
    assert apply_news_attribution(output(draft, material, text, meta))['attribution'] == ''


def test_article_text_that_is_not_verified_evidence_cannot_authorize_a_fact_credit(tmp_path):
    _, text, meta, _, _ = make_source(tmp_path, opinions=False)
    draft, material = draft_material([])
    from script_agent.models import Fact, Claim
    material.facts = [Fact(id='f1', text='미검증 분석', quote='금리 기대 약화가 부담이라는 분석이다.', kind='source_statement', conditions=[])]
    draft.script = ['가일보에 따르면 부담이 커졌습니다.']
    draft.claims = [Claim(sentence=1, kind='fact', fact_ids=['f1'])]
    assert any('매체' in i.problem for i in news_checks(draft, material, text))
    assert apply_news_attribution(output(draft, material, text, meta))['attribution'] == ''
