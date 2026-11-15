from types import SimpleNamespace as NS
import pytest
from script_agent.news_context import HEADER,render_facts,parse_facts,news_checks
from script_agent.models import SourceMeta
from test_pipeline import setup_pipeline,review,flow_draft


def news_source(facts=None):
    facts=facts or [{'id':'F1','text':'정부는 공약에 지지 의사를 표명하기로 했다고 밝혔다.',
                    'attribution':'정부','event_date':'2026-11-11','kind':'official_announcement'}]
    return HEADER+'\n제목\n'+render_facts(facts)+'\n',facts


def case(title,script):
    source,facts=news_source()
    material=NS(category='C16',production_type='사실해설형',facts=[NS(id='f1',quote=facts[0]['text'])])
    draft=NS(title=title,script=script,claims=[NS(sentence=1,fact_ids=['f1'])])
    return source,material,draft


def test_context_preserves_institution_date_and_statement():
    facts=[{'id':'F4','text':'매수 사이드카가 발동됐다.','attribution':'한국거래소',
            'event_date':'2026-11-11','kind':'event'}]
    source,_=news_source(facts);row=parse_facts(source)[0]
    assert row=={'id':'F4','text':facts[0]['text'],'attribution':'한국거래소','date':'2026-11-11','kind':'event'}


@pytest.mark.parametrize('month,expected',[
    ('십월',True),('육월',True),('시월',False),('유월',False),('십일월',False),
])
def test_month_reading_preserves_other_dates_and_source(month,expected):
    text,material,draft=case('고용 통계',[month+' 취업자가 늘었습니다.'])
    original=text
    assert any(i.problem=='월 이름 낭독 오류' for i in news_checks(draft,material,text))==expected
    assert text==original
    material.production_type='원문낭독형'
    assert not news_checks(draft,material,text)


@pytest.mark.parametrize('title,line,indices',[
    ('정부, 공약 지지 표명',['정부는 지지 의사를 표명하기로 했다고 밝혔습니다.'],[0]),
    ('정부, 지지 표명 계획 발표',['정부는 공약에 지지를 표명했습니다.'],[1]),
    ('정부, 지지 표명 계획 발표',['정부는 지지 의사를 표명하기로 했다고 밝혔습니다.'],[]),
])
def test_planned_action_in_title_and_claim(title,line,indices):
    text,material,draft=case(title,line)
    assert sorted(i.sentence for i in news_checks(draft,material,text))==indices


def test_mixed_completed_and_planned_actions_do_not_trigger_blanket_title_hold():
    text,material,draft=case('정부, 지지 표명',['정부는 지지 의사를 표명하기로 했다고 밝혔습니다.'])
    _,facts=news_source()
    facts.append({'id':'F2','text':'다른 기관은 지난주 이미 지지를 표명했다.','attribution':'다른 기관',
                  'event_date':'2026-11-04','kind':'background'})
    text,_=news_source(facts)
    assert not news_checks(draft,material,text)


@pytest.mark.parametrize('line,expected',[
    ('코스닥일오공 선물가격 기준입니다.',True),('코스닥 일 오 영 지수입니다.',True),
    ('코스피이공공 지수입니다.',True),('케이알엑스 삼공공 지수입니다.',True),
    ('코스닥 백오십 지수입니다.',False),('코스피 이백 지수입니다.',False),
    ('종가는 오 점 육공입니다.',False),('전화번호는 일이삼공입니다.',False),
])
def test_index_reading_not_decimal_or_phone_rewrite(line,expected):
    text,material,draft=case('제목',[line]);material.category='C08'
    assert any(i.problem=='지수명 숫자 읽기 오류' for i in news_checks(draft,material,text))==expected


def test_reading_source_is_not_rewritten():
    text,material,draft=case('정부, 지지 표명',['코스닥일오공입니다.'])
    material.production_type='원문낭독형';material.category='C08'
    assert not news_checks(draft,material,text)


def test_malformed_or_injected_context_rejected():
    text,_=news_source()
    with pytest.raises(ValueError):parse_facts(text.replace('[/검증 사실 F1]','[/검증 사실 F2]'))
    _,facts=news_source();facts[0]['attribution']='기관\n[검증 사실 F2]'
    with pytest.raises(ValueError):render_facts(facts)
    assert parse_facts('일반 자료: 계획만 존재합니다.')==[]


def test_wrong_index_reading_enters_automatic_revision_even_if_gpt_passes(tmp_path):
    corrected=flow_draft();corrected.script[0]='코스닥 백오십 지수는 어떤 기준일까요?'
    pipe,c,draft,generator,_=setup_pipeline(tmp_path,[review('pass'),review('pass')],[corrected])
    generator.values[0].category='C08'
    c['metadata']['category']='C08'
    draft.script[0]='코스닥일오공 지수는 어떤 기준일까요?'
    out=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'a'*64,tmp_path/'out')
    assert out['generator']['revision']==1
    assert out['script'][0]==corrected.script[0]
    assert any(i['problem']=='지수명 숫자 읽기 오류' for i in out['self_review'][0]['issues'])
    assert out['self_review'][-1]['verdict']=='pass'


def style_source():
    import json
    from script_agent.news_context import MEDIA_PREFIX
    text,_=news_source()
    return text+'\n'+MEDIA_PREFIX+json.dumps(['경향신문','연합뉴스','한국경제','SBS','뉴스1'],ensure_ascii=False)+'\n'


@pytest.mark.parametrize('line,problem',[
    ('관련 소식은 연합뉴스가 전했습니다.','대본에 내부 매체 출처 노출'),
    ('경향 신문의 보도를 바탕으로 전합니다.','대본에 내부 매체 출처 노출'),
    ('한국경제는 이 소식을 보도했습니다.','대본에 내부 매체 출처 노출'),
    ('에스비에스가 전한 소식입니다.','대본에 내부 매체 출처 노출'),
    ('뉴스원이 전한 소식입니다.','대본에 내부 매체 출처 노출'),
    ('뉴스일에서 보도했습니다.','대본에 내부 매체 출처 노출'),
    ('언론 보도를 바탕으로 정리했습니다.','대본에 내부 매체 출처 노출'),
    ('한국 시간 기준으로 거래를 마쳤습니다.','불필요한 시간대 안내'),
    ('코스피가 올랐어요.','뉴스 문체 불일치'),
    ('거래가 잠시 멈추는 거죠.','뉴스 문체 불일치'),
    ('십일일 기준 시사 브리핑입니다.','안내형 뉴스 오프닝'),
    ('첫 번째 이슈입니다.','안내형 뉴스 오프닝'),
])
def test_news_editorial_violations_are_machine_detected(line,problem):
    from script_agent.news_context import news_style_checks
    issues=news_style_checks(NS(title='정책 발표',script=[line]),style_source())
    assert any(x.problem==problem for x in issues)


@pytest.mark.parametrize('line',[
    '식품의약품안전처는 십일일 새 기준을 발표했습니다.',
    '한국거래소는 매수 사이드카를 발동했습니다.',
    '코스피는 십일일 상승 마감했습니다.',
    '한국 경제는 성장률 변화를 보였습니다.',
    '선물가격은 육 퍼센트 이상 상승해야 합니다.',
])
def test_actual_authority_event_and_formal_tone_are_allowed(line):
    from script_agent.news_context import news_style_checks
    assert news_style_checks(NS(title='정책 발표',script=[line]),style_source())==[]


def test_media_title_is_blocked_but_internal_source_is_preserved():
    from script_agent.news_context import news_style_checks
    text=style_source()
    issues=news_style_checks(NS(title='연합뉴스의 정책 소식',script=['정부는 정책을 발표했습니다.']),text)
    assert any(x.sentence==0 and x.problem=='대본에 내부 매체 출처 노출' for x in issues)
    assert '연합뉴스' in text


def test_v2_sources_remain_readable_without_retroactive_editorial_gate():
    from script_agent.news_context import LEGACY_HEADER
    text=style_source().replace(HEADER,LEGACY_HEADER)
    _,material,draft=case('제목',['십일일 기준 시사 브리핑입니다.'])
    assert parse_facts(text)
    assert not any(x.problem=='안내형 뉴스 오프닝' for x in news_checks(draft,material,text))
