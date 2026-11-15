from datetime import date,datetime,timezone
from issue_collector.core import Extraction,Verification,target_day
from issue_collector.quality import validate_facts
from issue_collector.acquisition import canonical,xml_candidates,extract_article,in_window

DAY=date(2026,11,11)
def sample():
    quote='정부는 11일 새 제도를 발표했고 시행 시점을 내년으로 정했다.'
    articles=[{'id':'a','origin':'one','text':quote},{'id':'b','origin':'two','text':quote}]
    ext=Extraction.model_validate({'title':'발표','excluded':[],'facts':[{'id':'F1','text':'정부가 십일월 십일일 제도 변경을 알렸다.','event_date':'2026-11-11','date_basis':'relative_to_publication','kind':'official_announcement','involves_named_person':False,'attribution':'정부','evidence':[{'article_id':x['id'],'quote':quote} for x in articles]}]})
    ver=Verification.model_validate({'cautions':[],'checks':[{'fact_id':'F1','verdict':'verified','supported_article_ids':['a','b'],'independent_origins':['one','two'],'date_confirmed':True,'attribution_correct':True,'reason':'확인'}]})
    return ext,ver,articles

def test_valid():
    e,v,a=sample(); good,bad=validate_facts(e,v,a,DAY)
    assert len(good)==1 and not bad

def test_syndicated_copies_not_two_sources():
    e,v,a=sample(); a[1]['origin']='one'
    good,bad=validate_facts(e,v,a,DAY)
    assert not good and 'insufficient_independent_sources' in bad[0]['reasons']

def test_fabricated_quote_blocked():
    e,v,a=sample(); e.facts[0].evidence[1].quote='본문에 존재하지 않는 가짜 근거'
    assert not validate_facts(e,v,a,DAY)[0]

def test_missing_review_blocks():
    e,v,a=sample(); v.checks=[]
    assert 'missing_or_duplicate_check' in validate_facts(e,v,a,DAY)[1][0]['reasons']

def test_duplicate_review_blocks():
    e,v,a=sample(); v.checks*=2
    assert not validate_facts(e,v,a,DAY)[0]

def test_publication_date_does_not_make_old_event_current():
    e,v,a=sample(); e.facts[0].event_date='2026-11-10'
    assert not validate_facts(e,v,a,DAY)[0]

def test_unverified_and_conflicting_facts_fail():
    e,v,a=sample(); v.checks[0].verdict='conflict'
    assert not validate_facts(e,v,a,DAY)[0]

def test_opinion_blocked_even_if_model_approves():
    e,v,a=sample(); e.facts[0].kind='opinion'
    assert not validate_facts(e,v,a,DAY)[0]

def test_unknown_date_blocked():
    e,v,a=sample(); e.facts[0].date_basis='unknown'
    assert not validate_facts(e,v,a,DAY)[0]

def test_invented_review_source_blocked():
    e,v,a=sample(); v.checks[0].supported_article_ids.append('fake')
    assert not validate_facts(e,v,a,DAY)[0]

def test_named_person_needs_attribution():
    e,v,a=sample(); e.facts[0].involves_named_person=True;e.facts[0].attribution=''
    assert not validate_facts(e,v,a,DAY)[0]

def test_kst_day_rollover():
    assert target_day(instant=datetime(2026,11,11,15,5,tzinfo=timezone.utc))==DAY
    assert in_window('2026-11-10T15:00:00Z',DAY)
    assert not in_window('2026-11-11T15:00:00Z',DAY)

def test_rss_namespaced_date_and_tracking():
    xml='''<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item><title>제목</title><link>https://www.hani.co.kr/arti/123.html?utm_source=rss</link><dc:date>2026-11-11T20:00:00+09:00</dc:date></item></channel></rss>'''
    rows=xml_candidates(xml,'https://www.hani.co.kr/rss/',DAY)
    assert len(rows)==1 and rows[0]['url'].endswith('123.html')

def test_html_disguised_as_rss_is_not_news():
    assert xml_candidates('<html><body>not RSS</body></html>','https://www.hani.co.kr/rss/',DAY)==[]

def test_extraction_has_no_navigation_fallback():
    item={'title':'t','published_at':None,'origin':'x'}
    assert extract_article('<html><nav>fake news text</nav></html>',item)['text']==''

def test_dtd_rejected():
    import pytest
    with pytest.raises(ValueError,match='xml_dtd_forbidden'):
        xml_candidates('<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss/>','https://www.hani.co.kr',DAY)

def test_specific_body_precedes_article_page_wrapper():
    item={'title':'t','published_at':'2026-11-11','origin':'hankyung'}
    body='정부는 새 정책을 발표했다. '*30
    html='<article>MENU '*100+'<div id="articletxt">'+body+'</div></article>'
    value=extract_article(html,item)
    assert 'MENU' not in value['text'] and '정부는' in value['text']

def test_credited_wire_report_collapses_origin():
    item={'title':'t','published_at':'2026-11-11','origin':'fnnews'}
    body='11일 뉴시스에 따르면 새 제도가 발표됐다. '+'확인한 보도 내용이다. '*30
    value=extract_article('<div class="article-body">'+body+'</div>',item)
    assert value['origin']=='newsis'

def test_extra_real_corroboration_does_not_invalidate_two_quotes():
    e,v,a=sample();a.append({'id':'c','origin':'three','text':a[0]['text']})
    v.checks[0].supported_article_ids.append('c');v.checks[0].independent_origins.append('three')
    good,bad=validate_facts(e,v,a,DAY)
    assert len(good)==1 and not bad
    assert {x['article_id'] for x in good[0]['evidence']}=={'a','b'}

def test_duplicate_factual_text_does_not_fill_quota():
    e,v,a=sample();e.facts.append(e.facts[0].model_copy(update={'id':'F2'}))
    v.checks.append(v.checks[0].model_copy(update={'fact_id':'F2'}))
    good,bad=validate_facts(e,v,a,DAY)
    assert not good and all('duplicate_fact_text' in x['reasons'] for x in bad)
