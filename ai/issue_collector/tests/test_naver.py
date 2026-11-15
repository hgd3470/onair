import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from issue_collector import acquisition, naver
from issue_collector.core import read
from issue_collector.llm import credentials

DAY = date(2026, 11, 11)
KEYS = {naver.NCP_ID: 'test-id', naver.NCP_KEY: 'test-secret'}


def item(number=1, **changes):
    return {'title': '<b>고용</b> &amp; 정책 발표', 'originallink': f'https://www.hani.co.kr/arti/{number}.html',
            'link': 'https://n.news.naver.com/article/028/1', 'pubDate': 'Wed, 11 Nov 2026 10:00:00 +0900',
            'description': '검색 요약은 본문 근거가 아니다', **changes}


class FakeFetcher:
    def __init__(self, tmp_path, pages):
        self.cache, self.pages, self.calls, self.events = tmp_path, iter(pages), [], []

    def _get(self, url, headers, discovery=False):
        self.calls.append((url, headers, discovery))
        data = next(self.pages)
        if isinstance(data, Exception):
            raise data
        return url, json.dumps(data)

    def event(self, url, method, **extra):
        self.events.append({'url': url, 'method': method, **extra})


def page(items, total=None):
    return {'items': items, 'total': len(items) if total is None else total}


def test_ncp_credentials_loaded_without_interpolation_and_environment_wins(tmp_path, monkeypatch):
    monkeypatch.setattr('issue_collector.providers.credentials', lambda **kw: {})
    for key in [naver.NCP_ID, naver.NCP_KEY, 'naver_client_id', 'naver_client_secret', 'NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET']:
        monkeypatch.delenv(key, raising=False)
    (tmp_path / '.env').write_text('X-NCP-APIGW-API-KEY-ID=file-id\nX-NCP-APIGW-API-KEY=${literal}\n')
    monkeypatch.setenv(naver.NCP_ID, 'env-id')
    result = credentials(tmp_path)
    assert result[naver.NCP_ID] == 'env-id'
    assert result[naver.NCP_KEY] == '${literal}'


def test_ncp_endpoint_headers_encoding_and_private_cache(tmp_path):
    f = FakeFetcher(tmp_path, [page([item()])])
    rows = naver.search_news(f, ['고용 정책'], DAY, {}, KEYS)
    url, headers, discovery = f.calls[0]
    assert url.startswith(naver.NCP_ENDPOINT + '?') and 'format=json' in url
    assert '%EA%B3%A0%EC%9A%A9' in url and 'start=1' in url and 'sort=date' in url
    assert headers == KEYS and discovery
    assert rows[0]['title'] == '고용 & 정책 발표'
    assert rows[0]['origin'] == 'hani' and rows[0]['naver_provided_at'] == item()['pubDate']
    assert 'description' not in rows[0] and 'text' not in rows[0]
    assert 'test-secret' not in json.dumps(f.events)
    assert 'test-secret' not in next((tmp_path/'naver').glob('*.json')).read_text()
    cached = FakeFetcher(tmp_path, [])
    assert naver.search_news(cached, ['고용 정책'], DAY, {}, KEYS) == rows
    assert not cached.calls and cached.events[0]['cached']


def test_legacy_compatibility_and_ncp_precedence(tmp_path):
    legacy = {'naver_client_id': 'legacy-id', 'naver_client_secret': 'legacy-secret'}
    f = FakeFetcher(tmp_path, [page([])])
    naver.search_news(f, ['q'], DAY, {}, legacy)
    assert f.calls[0][0].startswith(naver.LEGACY_ENDPOINT)
    assert f.calls[0][1]['X-Naver-Client-Id'] == 'legacy-id'
    assert naver.connection(legacy | KEYS)[0] == naver.NCP_ENDPOINT
    assert naver.connection(legacy | {naver.NCP_ID: 'partial'}) == (naver.NCP_ENDPOINT, None)


@pytest.mark.parametrize('keys,config,status', [({}, {}, 'skipped_missing_credentials'),
    (KEYS, {'naver': {'enabled': False}}, 'disabled')])
def test_no_unintended_calls(tmp_path, keys, config, status):
    f = FakeFetcher(tmp_path, [])
    assert not naver.search_news(f, ['q'], DAY, config, keys)
    assert not f.calls and f.events[0]['status'] == status


def test_pages_pass_newer_news_until_target_then_stop_at_older(tmp_path):
    f = FakeFetcher(tmp_path, [page([item(1, pubDate='Thu, 12 Nov 2026 01:00:00 +0900')], 100),
                              page([item(2)], 100), page([item(3, pubDate='Tue, 10 Nov 2026 23:59:59 +0900')], 100)])
    rows = naver.search_news(f, ['q'], DAY, {'naver': {'display': 1}}, KEYS)
    assert len(rows) == 1 and rows[0]['url'].endswith('/2.html')
    assert 'start=3' in f.calls[-1][0] and len(f.calls) == 3
    assert f.events[-1]['stop'] == 'older_than_target_day'


def test_invalid_items_do_not_discard_valid_siblings_or_promote_naver_copy(tmp_path):
    entries = [None, item(pubDate=None), item(pubDate='invalid'), item(originallink='https://evil.invalid/x'),
               item(originallink='https://www.hani.co.kr:bad/x'), item(originallink=''),
               item(originallink='http://user:secret@www.hani.co.kr/x'),
               item(pubDate='Tue, 10 Nov 2026 14:59:59 +0000'),
               item(pubDate='Wed, 11 Nov 2026 15:00:00 +0000'),
               item(10, pubDate='Tue, 10 Nov 2026 15:00:00 +0000'), item(11, title=None),
               item(12, originallink='', link='https://www.mk.co.kr/news/1')]
    f = FakeFetcher(tmp_path, [page(entries)])
    rows = naver.search_news(f, ['q'], DAY, {}, KEYS)
    assert len(rows) == 2
    assert {r['origin'] for r in rows} == {'hani', 'mk'}


def test_duplicate_urls_queries_and_pages_are_bounded(tmp_path):
    data = page([item(1, originallink='https://www.hani.co.kr/arti/1.html?utm_source=a')], 1000)
    f = FakeFetcher(tmp_path, [data, data])
    rows = naver.search_news(f, ['q', 'q'], DAY, {'naver': {'display': 1}}, KEYS)
    assert len(rows) == 1 and len(f.calls) == 2
    assert f.events[-1]['stop'] == 'repeated_page' and f.events[-1]['coverage_limited']


def test_page_limit_reported_and_topic_budget_used(tmp_path):
    f = FakeFetcher(tmp_path, [page([item(1)], 100)])
    naver.search_news(f, ['q'], DAY, {'naver': {'display': 1, 'topic_max_pages': 1}}, KEYS, topic=True)
    assert len(f.calls) == 1 and f.events[-1]['stop'] == 'page_limit'


@pytest.mark.parametrize('status', [401, 403, 429])
def test_auth_and_quota_errors_stop_remaining_queries(tmp_path, status):
    f = FakeFetcher(tmp_path, [ValueError(f'http_{status}')])
    assert not naver.search_news(f, ['q', 'other'], DAY, {}, KEYS)
    assert not naver.search_news(f, ['topic'], DAY, {}, KEYS, topic=True)
    assert len(f.calls) == 1 and f.events[0]['code'] == f'http_{status}'


def test_transient_server_retry_is_bounded_and_raw_errors_not_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(naver.time, 'sleep', lambda _: None)
    f = FakeFetcher(tmp_path, [ValueError('http_503'), page([item()])])
    assert len(naver.search_news(f, ['q'], DAY, {}, KEYS)) == 1
    assert f.events[0]['attempts'] == 2
    other = FakeFetcher(tmp_path/'other', [RuntimeError('test-secret reflected by upstream')])
    assert not naver.search_news(other, ['q'], DAY, {}, KEYS)
    assert other.events[0]['code'] == 'RuntimeError' and 'test-secret' not in json.dumps(other.events)
    limited = FakeFetcher(tmp_path/'limited', [ValueError('http_503')]*3)
    assert not naver.search_news(limited, ['q'], DAY, {}, KEYS)
    assert len(limited.calls) == 3


@pytest.mark.parametrize('bad', [{'errorCode': 'bad'}, {'items': {}, 'total': 1}, {'items': [], 'total': -1}])
def test_invalid_response_not_cached(tmp_path, bad):
    f = FakeFetcher(tmp_path, [bad])
    assert not naver.search_news(f, ['q'], DAY, {}, KEYS)
    assert f.events[0]['code'] == 'naver_invalid_response'
    assert not list(tmp_path.rglob('*.json'))


@pytest.mark.parametrize('cfg', [{'display': 101}, {'display': True}, {'max_pages': 11}, {'retries': 3}])
def test_bad_settings_rejected(cfg):
    with pytest.raises(ValueError, match='naver_invalid'):
        naver.settings({'naver': cfg})


def test_credentialed_redirect_never_forwards_keys(tmp_path, monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda *a: [(None, None, None, None, ('8.8.8.8', 443))])
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': 'https://www.hani.co.kr/steal'})
    f = acquisition.Fetcher(tmp_path, transport=httpx.MockTransport(handler))
    rows = naver.search_news(f, ['q'], DAY, {}, KEYS)
    assert not rows and len(calls) == 1
    assert calls[0].url.host == 'naverapihub.apigw.ntruss.com'
    assert f.events[0]['code'] == 'credentialed_redirect_blocked'


def test_api_host_only_allowed_for_discovery(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda *a: [(None, None, None, None, ('8.8.8.8', 443))])
    assert acquisition.secure_url(naver.NCP_ENDPOINT, discovery=True)
    with pytest.raises(ValueError, match='unregistered'):
        acquisition.secure_url(naver.NCP_ENDPOINT)


def test_search_timestamp_never_substitutes_for_article_publication():
    c = acquisition.candidate(item()['originallink'], 'title', item()['pubDate'], 'naver_api')
    html = '<article>' + '실제 기사 본문입니다. ' * 30 + '</article>'
    assert acquisition.extract_article(html, c)['published_at'] is None
    html += '<script type="application/ld+json">' + json.dumps({'@type': 'NewsArticle', 'datePublished': '2026-11-10T20:00:00+09:00'}) + '</script>'
    assert acquisition.extract_article(html, c)['published_at'] == '2026-11-10T20:00:00+09:00'


def test_discover_includes_naver_without_feeds(tmp_path, monkeypatch):
    monkeypatch.setattr(acquisition, 'FEEDS', [])
    monkeypatch.setattr(acquisition, 'LISTINGS', [])
    f = FakeFetcher(tmp_path, [page([item()])])
    cfg = {'http_workers': 1, 'max_candidates': 10, 'naver': {'issue_queries': ['고용']}}
    rows = acquisition.discover(f, 'issue', DAY, cfg, KEYS)
    assert len(rows) == 1 and rows[0]['method'] == 'naver_api'


def test_topic_query_removes_target_date_without_removing_other_dates():
    assert naver.topic_query('2026-11-11 고용동향 발표', DAY) == '고용동향 발표'
    assert naver.topic_query('2026년 11월 11일 고용 발표', DAY) == '고용 발표'
    assert naver.topic_query('2027-01-01 시행 정책', DAY) == '2027-01-01 시행 정책'


def test_origin_diversity_before_article_limit():
    from issue_collector.pipeline import diverse_origins
    rows = [{'id': i, 'origin': origin} for i, origin in enumerate(['a', 'a', 'a', 'b', 'b', 'c'])]
    assert [r['origin'] for r in diverse_origins(rows)[:3]] == ['a', 'b', 'c']


def test_topic_naver_supplement_supplies_missing_independent_sources(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from issue_collector import pipeline
    from issue_collector.core import Selection, Topic
    from test_quality import sample
    extraction, verification, articles = sample()
    fact = extraction.facts[0]
    extraction.facts = [fact.model_copy(update={'id': f'F{i}', 'text': f'검증된 개별 사실 {i}입니다.'}) for i in range(1, 4)]
    check = verification.checks[0]
    verification.checks = [check.model_copy(update={'fact_id': f'F{i}'}) for i in range(1, 4)]
    verification.topic_confirmed = True
    verification.central_fact_ids = ['F1']
    for i, article in enumerate(articles):
        article.update(url=f'https://www.hani.co.kr/arti/{i}.html', title='공식 발표', publisher=f'편집국 {i}')
    extra = {**articles[-1], 'id': 'third', 'url': 'https://www.mk.co.kr/news/3', 'origin': 'third'}
    topic = Topic(id='policy', title='공식 발표', reason='영향', importance=4,
                  candidate_ids=[articles[0]['id']], search_query='2026-11-11 고용 발표')
    class FakeModels:
        def __init__(self, *args): pass
        def call(self, stage, *args):
            return {'select': Selection(topics=[topic], coverage_notes=[]), 'extract': extraction, 'verify': verification}[stage]
        def search(self, *args):
            pytest.fail('Google search is unnecessary after Naver supplies three origins')
    calls = []
    def search(fetcher, queries, day, config, keys, *, topic):
        calls.append(queries)
        return [articles[1], extra]
    monkeypatch.setattr(pipeline, 'Models', FakeModels)
    monkeypatch.setattr(pipeline, 'discover', lambda *args: [articles[0]])
    monkeypatch.setattr(pipeline, 'search_news', search)
    monkeypatch.setattr(pipeline, 'acquire', lambda fetcher, row, *args: row)
    cfg = {'collection': {'http_timeout_sec': 1, 'candidate_topics': 1, 'max_articles_per_topic': 8,
                         'http_workers': 2, 'min_independent_sources': 2, 'google_search': True, 'min_facts': 3},
           'script': {'target_sec': 60}}
    result = pipeline.collect_stream(tmp_path, cfg, KEYS, DAY, 'issue', tmp_path/'run', 1)
    assert result['status'] == 'complete' and calls == [['고용 발표']]


def test_short_jsonld_teaser_does_not_hide_full_article():
    c = acquisition.candidate('https://view.asiae.co.kr/article/123', 'title', method='naver_api')
    body = '정부가 노동시장과 산업 격차에 대응하는 정책을 발표했다. ' * 12
    html = ('<script type="application/ld+json">' + json.dumps({'@type': 'NewsArticle', 'articleBody': '이것은 짧은 요약이다.',
            'datePublished': '2026-11-11T09:00:00+09:00'}) + '</script>'
            '<main><div itemprop="articleBody">' + body + '</div><div class="article-body">관련 기사 목록</div></main>')
    result = acquisition.extract_article(html, c)
    assert result['text'] == body.strip() and result['published_at'] == '2026-11-11T09:00:00+09:00'


def test_explicit_article_body_excludes_navigation_and_short_alerts():
    c = acquisition.candidate('https://www.edaily.co.kr/News/Read?newsId=1', 'title', method='naver_api')
    body = '코스피가 외국인의 순매수에 힘입어 상승세로 거래를 마쳤다. ' * 12
    html = '<nav>메뉴 ' * 1 + '</nav><div itemprop="articleBody">' + body + '</div><aside>추천 기사 광고</aside>'
    assert acquisition.extract_article(html, c)['text'] == body.strip()
    assert acquisition.extract_article('<div itemprop="articleBody">지수 상승 마감</div><nav>' + body + '</nav>', c)['text'] == ''


def test_publisher_specific_selector_not_used_on_other_publishers():
    body = '국내 증시가 전 거래일보다 상승하며 거래를 마쳤다. ' * 12
    html = '<div class="paragraph">' + body + '</div>'
    assert acquisition.extract_article(html, acquisition.candidate('https://www.ytn.co.kr/_ln/1'))['text'] == body.strip()
    assert acquisition.extract_article(html, acquisition.candidate('https://www.hani.co.kr/arti/1'))['text'] == ''
