"""Bounded RSS / listing / optional news API discovery and article acquisition."""
import concurrent.futures
import email.utils
import ipaddress
import json
import re
import socket
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
import httpx
from bs4 import BeautifulSoup
from .catalog import FEEDS, LISTINGS, publisher
from .core import KST, digest, norm, now, read, save, window

UA = 'TeumIssueCollector/0.1 (+fact-checking research; no article republication)'

def canonical(url):
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k not in {'fbclid','gclid'}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, urlencode(query), ''))

def parse_date(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return None
    return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt.astimezone(KST)

def in_window(value, day):
    dt = parse_date(value)
    a, b = window(day)
    return dt is not None and a <= dt < b

def secure_url(url, *, discovery=False):
    p = urlsplit(url)
    if p.scheme not in {'https', 'http'} or not p.hostname or p.username or p.password or p.port not in {None,80,443}:
        raise ValueError('unsafe_url')
    if not publisher(url) and not (discovery and p.hostname in {'openapi.naver.com','naverapihub.apigw.ntruss.com','vertexaisearch.cloud.google.com'}):
        raise ValueError('unregistered_publisher')
    for row in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80)):
        if not ipaddress.ip_address(row[4][0]).is_global:
            raise ValueError('nonpublic_address')
    return url

class Fetcher:
    def __init__(self, cache, timeout=25, transport=None):
        self.cache = cache
        self.timeout = timeout
        self.transport = transport
        self.robots = {}
        self.locks = {}
        self.guard = threading.Lock()
        self.last = {}
        self.events = []

    def _get(self, url, headers=None, discovery=False, check_redirect_robots=False):
        for _ in range(5):
            secure_url(url, discovery=discovery)
            host = urlsplit(url).netloc
            with self.guard:
                lock = self.locks.setdefault(host, threading.Lock())
            with lock:
                time.sleep(max(0, .4 - (time.monotonic() - self.last.get(host, 0))))
                with httpx.Client(timeout=self.timeout, transport=self.transport, headers={'User-Agent': UA}) as client:
                    with client.stream('GET', url, headers=headers) as r:
                        if r.is_redirect:
                            nxt = urljoin(url, r.headers['location'])
                            # API credentials must never be forwarded across origins.
                            if headers:
                                raise ValueError('credentialed_redirect_blocked')
                            if check_redirect_robots and not self.allowed(nxt):
                                raise ValueError('redirect_robots_denied')
                            url = nxt
                            continue
                        if r.status_code >= 400:
                            raise ValueError(f'http_{r.status_code}')
                        data = bytearray()
                        for chunk in r.iter_bytes():
                            data.extend(chunk)
                            if len(data) > 4_000_000:
                                raise ValueError('response_too_large')
                        text = bytes(data).decode(r.encoding or 'utf-8', errors='replace')
                self.last[host] = time.monotonic()
            return url, text
        raise ValueError('redirect_limit')

    def allowed(self, url):
        p = urlsplit(url)
        origin = f'{p.scheme}://{p.netloc}'
        with self.guard:
            policy = self.robots.get(origin)
        if policy is None:
            try:
                _, body = self._get(origin + '/robots.txt')
                policy = RobotFileParser()
                policy.parse(body.splitlines())
            except ValueError as exc:
                # Missing robots permits retrieval; network/403 failures do not.
                policy = str(exc) in {'http_404', 'http_410'}
            except Exception:
                policy = False
            with self.guard:
                self.robots[origin] = policy
        return policy if isinstance(policy, bool) else policy.can_fetch(UA, url)

    def get(self, url):
        url = canonical(url)
        path = self.cache / (digest(url) + '.json')
        if path.exists():
            record = read(path)
            return record['url'], record['text']
        if not self.allowed(url):
            raise ValueError('robots_denied_or_unavailable')
        final, text = self._get(url, check_redirect_robots=True)
        if final != url and not self.allowed(final):
            raise ValueError('redirect_robots_denied')
        save(path, {'url': final, 'text': text, 'retrieved_at': now(), 'sha256': digest(text)})
        return final, text

    def event(self, url, method, **extra):
        with self.guard:
            self.events.append({'url': url, 'method': method, 'at': now(), **extra})

def xml_candidates(text, feed_url, day):
    # Reject entities and DTDs; only self-contained RSS/Atom accepted.
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', text, re.I):
        raise ValueError('xml_dtd_forbidden')
    root = ET.fromstring(text)
    result = []
    for node in root.iter():
        if node.tag.split('}')[-1] not in {'item', 'entry'}:
            continue
        values = {}
        for e in node:
            tag = e.tag.split('}')[-1]
            values[tag] = e.attrib.get('href') if tag == 'link' and e.attrib.get('href') else ''.join(e.itertext()).strip()
        url = values.get('link', '')
        when = values.get('pubDate') or values.get('date') or values.get('published') or values.get('updated')
        if not publisher(url) or not in_window(when, day):
            continue
        result.append(candidate(url, norm(BeautifulSoup(values.get('title',''), 'html.parser').get_text()), when, 'rss', feed_url))
    return result

def candidate(url, title='', published=None, method='search', discovery_url=None):
    url = canonical(url)
    pub = publisher(url)
    return {'id': 'A' + digest(url)[:14], 'url': url, 'title': title, 'published_at': published,
            'publisher': pub[0] if pub else '', 'origin': pub[1] if pub else '',
            'official': pub[2] if pub else False, 'method': method, 'discovery_url': discovery_url or url}

def listing_candidates(html, url, day):
    soup = BeautifulSoup(html, 'html.parser')
    result = []
    for a in soup.select('a[href]'):
        link = canonical(urljoin(url, a['href']))
        title = norm(a.get_text(' ', strip=True))
        if not publisher(link) or len(title) < 12:
            continue
        # Only article routes, never arbitrary navigation/search links.
        if re.search(r'/article/|/view/|/news/202\d/.+\.(?:html|shtml)|newsId=|nttId=|no010101/\d+|newsid=', link):
            result.append(candidate(link, title[:200], None, 'listing', url))
    return result[:100]

def discover(fetcher, stream, day, config, keys=None):
    tasks = [('rss', u) for s,u in FEEDS if s in {stream,'both'}]
    tasks += [('listing', u.replace('/2026/', f'/{day.year}/')) for s,u in LISTINGS if s == stream]
    def one(task):
        method, url = task
        try:
            _, text = fetcher.get(url)
            found = xml_candidates(text,url,day) if method == 'rss' else listing_candidates(text,url,day)
            fetcher.event(url, method, status='ok', candidates=len(found))
            return found[:config['max_feed_items']]
        except Exception as exc:
            code = str(exc) if isinstance(exc,ValueError) and re.fullmatch('[a-z0-9_]+',str(exc)) else type(exc).__name__
            fetcher.event(url,method,status='failed',code=code)
            return []
    results = []
    with concurrent.futures.ThreadPoolExecutor(config['http_workers']) as pool:
        for batch in pool.map(one,tasks):
            results.extend(batch)
    from .naver import discover_news
    results.extend(discover_news(fetcher, stream, day, config, keys or {}))
    unique={c['url']:c for c in results}
    # Interleave publishers to avoid a high-volume feed monopolizing the model context.
    buckets={}
    for c in unique.values(): buckets.setdefault(c['publisher'],[]).append(c)
    ordered=[]
    while any(buckets.values()) and len(ordered)<config['max_candidates']:
        for bucket in buckets.values():
            if bucket: ordered.append(bucket.pop(0))
    return ordered[:config['max_candidates']]

def extract_article(html, item):
    soup = BeautifulSoup(html, 'html.parser')
    meta = {}
    for node in soup.select('meta'):
        meta[node.get('property') or node.get('name') or ''] = node.get('content','')
    published = meta.get('article:published_time') or meta.get('datePublished') or meta.get('date') or (item.get('published_at') if item.get('method') != 'naver_api' else None)
    # JSON-LD often contains the best article body/date, but never execute scripts.
    bodies = []
    for script in soup.select('script[type="application/ld+json"]'):
        try: obj=json.loads(script.string or script.get_text())
        except (ValueError,TypeError): continue
        stack = obj if isinstance(obj,list) else [obj]
        while stack:
            x=stack.pop()
            if not isinstance(x,dict): continue
            stack.extend(x.get('@graph',[]))
            types=x.get('@type',[])
            types=[types] if isinstance(types,str) else types
            if not any(t in {'NewsArticle','Article','ReportageNewsArticle'} for t in types): continue
            if x.get('datePublished') and not published: published=x['datePublished']
            if isinstance(x.get('articleBody'),str):
                body=norm(BeautifulSoup(x['articleBody'],'html.parser').get_text(' '))
                # Some publishers put only a short teaser in JSON-LD. It must
                # not suppress the full article body available in the DOM.
                if len(body)>=150: bodies.append(body)
    for x in soup.select('script,style,nav,header,footer,aside,form,figure,.ad,.advertisement'): x.decompose()
    host=(urlsplit(item.get('url','')).hostname or '').lower()
    specific=[]
    if host=='ytn.co.kr' or host.endswith('.ytn.co.kr'): specific=['.paragraph']
    if host=='kbs.co.kr' or host.endswith('.kbs.co.kr'): specific=['.view_con_text']
    selectors=specific+['[itemprop="articleBody"]','#txt_area','.news_body','#articleBody','#article-body','#article_body','#articletxt','#newsEndContents','.article_txt','.article-body','.article_body','.article-body-content','.story-news','.go_trans','.news_txt','.view_con','.view_cont','article']
    for sel in selectors:
        if bodies: break
        for node in soup.select(sel):
            text=norm(node.get_text(' ',strip=True))
            if len(text)>=150: bodies.append(text)
    text=max(bodies,key=len) if bodies else ''
    title=meta.get('og:title') or item['title']
    origin=item['origin']
    # Syndication is not independent reporting; collapse all credited wire copies.
    for label, wire in [('연합뉴스','yonhap'),('뉴시스','newsis'),('뉴스1','news1'),('로이터','reuters'),('AP통신','ap')]:
        if re.search(r'(?:=\s*'+label+r'|'+label+r'\s*(?:제공|기자|\)|사진|자료|에 따르면)|출처\s*[:：]?\s*'+label+r')',text):
            origin=wire
            break
    return {**item,'title':title,'published_at':published,'text':text,'origin':origin,'retrieved_at':now(),
            'body_sha256':digest(text),'rights_status':'unverified','extraction':'html_article_body'}

def acquire(fetcher, item, day, config):
    try:
        final, html=fetcher.get(item['url'])
        actual=candidate(final,item['title'],item.get('published_at'),item['method'],item.get('discovery_url'))
        actual['id']=item['id']
        result=extract_article(html,actual)
        if not result['text'] or len(result['text'])<150: raise ValueError('article_body_unavailable')
        if not in_window(result['published_at'],day): raise ValueError('publication_outside_target_day_or_unknown')
        if len(result['text'])>config['max_article_chars']:
            raise ValueError('article_body_too_large')
        fetcher.event(item['url'],'article',status='ok',chars=len(result['text']))
        return result
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[a-z0-9_]+',str(exc)) else type(exc).__name__
        fetcher.event(item['url'],'article',status='failed',code=code)
        return None
