"""Bounded Naver news discovery. Search snippets are never article evidence."""
import json
import re
import time
from collections import Counter
from urllib.parse import urlencode, urlsplit

from bs4 import BeautifulSoup

from .acquisition import candidate, in_window, parse_date
from .catalog import publisher
from .core import digest, norm, read, save, window

NCP_ENDPOINT = 'https://naverapihub.apigw.ntruss.com/search/v1/news'
LEGACY_ENDPOINT = 'https://openapi.naver.com/v1/search/news.json'
NCP_ID = 'X-NCP-APIGW-API-KEY-ID'
NCP_KEY = 'X-NCP-APIGW-API-KEY'
DEFAULT_QUERIES = {
    'issue': ['정부 정책 발표', '국무회의', '국회 본회의', '재난 안전', '고용 임금', '대출 금리', '세금 연금', '주거 물가', '보건복지부', '교육부'],
    'market': ['코스피 마감', '코스닥 마감', '원달러 환율', '국고채 금리', '한국은행', '수출 통계', '한국은행 통계'],
}


def connection(keys):
    # Never send NCP credentials to the developer-center API (or vice versa).
    if keys.get(NCP_ID) or keys.get(NCP_KEY):
        if not (keys.get(NCP_ID) and keys.get(NCP_KEY)):
            return NCP_ENDPOINT, None
        return NCP_ENDPOINT, {NCP_ID: keys[NCP_ID], NCP_KEY: keys[NCP_KEY]}
    if keys.get('naver_client_id') and keys.get('naver_client_secret'):
        return LEGACY_ENDPOINT, {'X-Naver-Client-Id': keys['naver_client_id'],
                                 'X-Naver-Client-Secret': keys['naver_client_secret']}
    return NCP_ENDPOINT, None


def settings(config):
    cfg = config.get('naver', {})
    result = {'enabled': cfg.get('enabled', True), 'display': cfg.get('display', 100),
              'max_pages': cfg.get('max_pages', 10), 'topic_max_pages': cfg.get('topic_max_pages', 3),
              'retries': cfg.get('retries', 2)}
    for key, lo, hi in [('display', 1, 100), ('max_pages', 1, 10), ('topic_max_pages', 1, 10), ('retries', 0, 2)]:
        value = result[key]
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError('naver_invalid_' + key)
    return result


def error_code(exc):
    # Do not persist raw HTTP errors, headers or provider response bodies.
    code = str(exc)
    allowed = {'credentialed_redirect_blocked', 'response_too_large', 'naver_invalid_response'}
    return code if code in allowed or re.fullmatch(r'http_[1-5][0-9]{2}', code) else type(exc).__name__


def response_items(data):
    if (not isinstance(data, dict) or not isinstance(data.get('items'), list)
            or type(data.get('total')) is not int or data['total'] < 0):
        raise ValueError('naver_invalid_response')
    return data['items']


def search_news(fetcher, queries, day, config, keys, *, topic=False):
    """Query latest-first pages, filter KST day, retain only registered original URLs.

    A per-fetcher circuit stops later queries after auth/quota failure. Successful
    pages alone are cached under the run's private cache; credentials are excluded.
    """
    cfg = settings(config)
    endpoint, headers = connection(keys)
    if not cfg['enabled'] or not headers:
        fetcher.event(endpoint, 'naver_api', status='disabled' if not cfg['enabled'] else 'skipped_missing_credentials')
        return []
    if getattr(fetcher, '_naver_blocked', False):
        return []
    found = {}
    start_day, _ = window(day)
    for query in dict.fromkeys(norm(q) for q in queries if isinstance(q, str) and norm(q)):
        seen_pages = set()
        max_pages = cfg['topic_max_pages'] if topic else cfg['max_pages']
        for page in range(max_pages):
            start = 1 + page * cfg['display']
            params = {'query': query, 'display': cfg['display'], 'start': start, 'sort': 'date'}
            if endpoint == NCP_ENDPOINT:
                params['format'] = 'json'
            url = endpoint + '?' + urlencode(params)
            cache = fetcher.cache / 'naver' / (digest({'url': url, 'day': day.isoformat(), 'version': 1}) + '.json')
            cached = False
            attempts = 0
            try:
                if cache.exists():
                    data = read(cache)
                    response_items(data)
                    cached = True
                else:
                    for attempt in range(cfg['retries'] + 1):
                        attempts += 1
                        try:
                            _, text = fetcher._get(url, headers, discovery=True)
                            data = json.loads(text)
                            response_items(data)
                            break
                        except Exception as exc:
                            code = error_code(exc)
                            if code not in {'http_500', 'http_502', 'http_503', 'http_504'} or attempt == cfg['retries']:
                                raise
                            time.sleep(0.5 * (2 ** attempt))
                    save(cache, data)
                items = response_items(data)
                counts = Counter()
                dates = []
                for item in items:
                    if not isinstance(item, dict):
                        counts['malformed'] += 1
                        continue
                    when = item.get('pubDate')
                    try:
                        date = parse_date(when) if isinstance(when, str) else None
                    except (ValueError, TypeError, OverflowError):
                        date = None
                    dates.append(date)
                    if date is None:
                        counts['invalid_date'] += 1
                        continue
                    if not in_window(when, day):
                        counts['outside_day'] += 1
                        continue
                    # Empty originallink can fall back only to a registered publisher,
                    # never to Naver-hosted copies or an arbitrary third-party domain.
                    original = item.get('originallink') or item.get('link')
                    try:
                        p = urlsplit(original) if isinstance(original, str) else None
                        valid = (p and p.scheme in {'http', 'https'} and p.hostname
                                 and not p.username and not p.password and p.port in {None, 80, 443}
                                 and publisher(original))
                    except ValueError:
                        valid = False
                    if not valid:
                        counts['unregistered_or_unsafe_url'] += 1
                        continue
                    title = item.get('title')
                    if not isinstance(title, str) or not norm(title):
                        counts['malformed'] += 1
                        continue
                    c = candidate(original, norm(BeautifulSoup(title, 'html.parser').get_text(' ', strip=True)),
                                  when, 'naver_api', url)
                    # This timestamp is discovery metadata, not proof of publication.
                    c['naver_provided_at'] = when
                    if c['url'] in found:
                        counts['duplicate'] += 1
                    else:
                        found[c['url']] = c
                        counts['accepted'] += 1
                signature = digest(items)
                repeated = bool(items) and signature in seen_pages
                seen_pages.add(signature)
                stop = None
                if not items or start + len(items) > data['total'] or len(items) < cfg['display']:
                    stop = 'exhausted'
                elif repeated:
                    stop = 'repeated_page'
                elif len(dates) == len(items) and all(d is not None and d < start_day for d in dates):
                    stop = 'older_than_target_day'
                elif page + 1 == max_pages:
                    stop = 'page_limit'
                fetcher.event(endpoint, 'naver_api', status='ok', query=query, start=start, cached=cached,
                              attempts=attempts, returned=len(items), candidates=counts['accepted'],
                              filtered=dict(counts), stop=stop, coverage_limited=stop in {'page_limit', 'repeated_page'})
                if stop:
                    break
            except Exception as exc:
                code = error_code(exc)
                fetcher.event(endpoint, 'naver_api', status='failed', query=query, start=start,
                              attempts=attempts, code=code)
                if code in {'http_401', 'http_403', 'http_429'}:
                    fetcher._naver_blocked = True
                    return list(found.values())
                break
    return list(found.values())


def topic_query(query, day):
    # API has no date-range parameter. A date literal acts as a content keyword;
    # use the publication window instead, without changing the Google query.
    query = query.replace(day.isoformat(), ' ')
    query = re.sub(rf'{day.year}년\s*0?{day.month}월\s*0?{day.day}일', ' ', query)
    return norm(query)


def discover_news(fetcher, stream, day, config, keys):
    queries = config.get('naver', {}).get(stream + '_queries', DEFAULT_QUERIES[stream])
    if not isinstance(queries, list) or not all(isinstance(q, str) and q.strip() for q in queries) or len(queries) > 20:
        raise ValueError('naver_invalid_queries')
    return search_news(fetcher, queries, day, config, keys)
