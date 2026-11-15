"""Versioned factual handoff and conservative, source-linked news checks.

The prose remains exact source text for quote validation. Labels are context,
not claims that an event has completed and not text to be read aloud.
"""
import re
import hashlib
import json
from .models import Issue
from .fact_source import FACTS_HEADER

ORIGINAL_HEADER = '이슈 수집 에이전트 · 원문 인계 v5'
from .news_opinions import HEADER as OPINION_HEADER, opinion_checks
ORIGINAL_HEADERS = (ORIGINAL_HEADER + '\n', OPINION_HEADER + '\n')

ORIGINALS_HEADER = '## 수집 원문'
ORIGINAL_BLOCK = re.compile(r'^### 원문 ([1-9][0-9]*)\n(\{[^\n]+\})\n\n(`{3,})text\n(.*?)\n\3\n', re.M | re.S)

LEGACY_HEADER = '이슈 수집 에이전트 · 사실 인계 v2'
HEADER = '이슈 수집 에이전트 · 사실 인계 v3'
MEDIA_PREFIX = '검증용 보도 매체: '
MEDIA_PREFIXES = (MEDIA_PREFIX, '내부 검증용 보도 매체(낭독 금지): ')
BLOCK = re.compile(r'^\[검증 사실 (?P<id>F[0-9]+)\]\n(?P<text>.*?)\n귀속 주체: (?P<attribution>[^\n]*)\n사건·발표 기준일: (?P<date>[^\n]+)\n사실 유형: (?P<kind>[^\n]+)\n\[/검증 사실 (?P=id)\]$', re.M | re.S)
FUTURE = re.compile(r'예정|계획|하기로|방침|추진|검토|예고')
PLANNED_ACTION = re.compile(r'([가-힣]{2,12}?)(?:하기로|할\s*(?:계획|예정)|\s+예정)')
INDEX_READINGS = (
    (r'코스닥\s*일\s*오\s*[공영]', '코스닥 백오십'),
    (r'코스피\s*이\s*[공영]\s*[공영]', '코스피 이백'),
    (r'케이\s*알\s*엑스\s*삼\s*[공영]\s*[공영]', '케이알엑스 삼백'),
)


def render_facts(facts):
    rows=[]
    for f in facts:
        if not re.fullmatch(r'F[0-9]+',f['id']):
            raise ValueError('invalid_handoff_fact_id')
        for key in ('text','attribution','event_date','kind'):
            if '\n' in f[key] or '\r' in f[key] or '[검증 사실' in f[key] or '[/검증 사실' in f[key]:
                raise ValueError('invalid_handoff_context')
        rows.append(f"[검증 사실 {f['id']}]\n{f['text']}\n귀속 주체: {f['attribution'] or '미확인'}\n"
                    f"사건·발표 기준일: {f['event_date']}\n사실 유형: {f['kind']}\n[/검증 사실 {f['id']}]")
    if len({f['id'] for f in facts})!=len(facts):
        raise ValueError('duplicate_handoff_fact_id')
    return '\n\n'.join(rows)


def original_bodies(text):
    if not text.startswith(ORIGINAL_HEADERS):
        return None
    _, marker, originals = text.partition('\n'+ORIGINALS_HEADER+'\n')
    bodies, ids, end = [], set(), 0
    for match in ORIGINAL_BLOCK.finditer(originals):
        if originals[end:match.start()].strip() or int(match[1]) != len(bodies) + 1:
            raise ValueError('invalid_news_originals')
        try:
            metadata = json.loads(match[2])
            article_id = metadata['article_id']
            body = match[4]
            if (not isinstance(article_id, str) or not article_id or article_id in ids
                    or not body.strip() or not metadata['source_url']
                    or metadata['body_sha256'] != hashlib.sha256(body.encode()).hexdigest()):
                raise ValueError('invalid_news_originals')
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError('invalid_news_originals') from None
        bodies.append(body)
        ids.add(article_id)
        end = match.end()
    if not marker or not bodies or originals[end:].strip():
        raise ValueError('invalid_news_originals')
    return bodies


def parse_facts(text):
    if text.startswith(ORIGINAL_HEADERS):
        original_bodies(text)
        text = text.partition('\n'+ORIGINALS_HEADER+'\n')[0]
    if not text.startswith((HEADER+'\n',LEGACY_HEADER+'\n',FACTS_HEADER+'\n',*ORIGINAL_HEADERS)):
        return []
    rows=[m.groupdict() for m in BLOCK.finditer(text)]
    if (not rows or text.count('[검증 사실 ')!=len(rows)
            or text.count('[/검증 사실 ')!=len(rows)
            or len({r['id'] for r in rows})!=len(rows)):
        raise ValueError('invalid_news_handoff')
    return rows


def news_checks(draft, material, text):
    issues=[]
    if material.production_type=='원문낭독형':
        return issues
    if text.startswith((HEADER+'\n',FACTS_HEADER+'\n',*ORIGINAL_HEADERS)):
        opinion_issues, allowed = opinion_checks(draft, material, text)
        issues.extend(opinion_issues)
        from .news_opinions import fact_publishers, publisher_names
        for n, publishers in fact_publishers(draft, material, text).items():
            allowed.setdefault(n, set()).update(alias for name in publishers for alias in publisher_names(name))
        issues.extend(news_style_checks(draft, text, allowed))
    # These are index identifiers, not decimals, dates, product codes or phone numbers.
    if material.category=='C08':
        for n,line in enumerate(draft.script,1):
            for pattern,reading in INDEX_READINGS:
                if re.search(pattern,line):
                    issues.append(Issue(sentence=n,problem='지수명 숫자 읽기 오류',
                        reason=f'지수명을 자리별 숫자로 읽음: {re.search(pattern,line)[0]}',
                        direction=f'{reading}로 읽고 수치·단위·비교 기준과 문장 길이를 유지'))
    rows=parse_facts(text)
    if not rows:
        return issues
    # A plan does not prove that the action has not occurred. Explicit negative
    # claims are semantic review items; never manufacture them as a correction.
    material_by_id={f.id:f for f in material.facts}
    # Only clear action + prospective grammar is deterministic. More subtle/mixed
    # modality (negation, proposed vs approved, enactment vs announcement) is for GPT.
    for row in rows:
        actions={m[1] for m in PLANNED_ACTION.finditer(row['text'])}
        if re.search(r'취소|철회|않|없',row['text']):
            continue
        evidence_quotes = []
        if text.startswith(ORIGINAL_HEADERS):
            prefix = f"근거 {row['id']}: "
            for line in text.partition('\n'+ORIGINALS_HEADER+'\n')[0].splitlines():
                if line.startswith(prefix):
                    evidence_quotes.extend(e['quote'] for e in json.loads(line[len(prefix):]))
        linked={fid for fid,f in material_by_id.items()
                if len(f.quote)>=10 and (f.quote in row['text'] or row['text'] in f.quote
                    or any(f.quote in quote or quote in f.quote for quote in evidence_quotes))}
        for action in actions:
            targets=[(c.sentence,draft.script[c.sentence-1]) for c in draft.claims
                     if 1<=c.sentence<=len(draft.script) and linked.intersection(c.fact_ids)]
            # Don't misclassify a mixed topic's completed action as the planned one.
            ambiguous=any(r is not row and action in r['text'] and not FUTURE.search(r['text']) for r in rows)
            if not ambiguous:
                targets.append((0,draft.title))
            for n,line in targets:
                if action in line and not FUTURE.search(line):
                    issues.append(Issue(sentence=n,problem='예정·완료 구분 누락',
                        reason=f"{row['id']}는 '{action}'의 계획·예정을 말하지만 제목/문장에서 그 구분이 빠짐",
                        direction='해당 행위의 계획·예정 상태를 명시하고 발표 사실과 행위 완료를 구분. 근거 없는 완료형을 쓰지 않기'))
    return list({(i.sentence,i.problem,i.reason):i for i in issues}.values())


def news_style_checks(draft, text, allowed=None):
    """Narration policy only; never strip internal provenance or rewrite source facts."""
    names=(['연합뉴스','뉴시스','뉴스1','한겨레','경향신문','한국경제','매일경제','파이낸셜뉴스',
            '이데일리','서울경제','아시아경제','KBS','MBC','SBS','YTN','JTBC','MBN','TV조선','채널A']
           if text.startswith(FACTS_HEADER+'\n') else [])
    context = text.partition('\n'+ORIGINALS_HEADER+'\n')[0] if text.startswith(ORIGINAL_HEADERS) else text
    for line in context.splitlines():
        prefix = next((p for p in MEDIA_PREFIXES if line.startswith(p)), None)
        if prefix:
            value=json.loads(line[len(prefix):])
            if not isinstance(value,list) or any(not isinstance(x,str) or not x.strip() for x in value):
                raise ValueError('invalid_internal_media_list')
            names.extend(value)
    aliases={'KBS':'케이비에스','MBC':'엠비씨','SBS':'에스비에스','YTN':'와이티엔',
             'JTBC':'제이티비씨','MBN':'엠비엔','TV조선':'티브이조선','채널A':'채널에이',
             '연합뉴스TV':'연합뉴스티브이','뉴스1':'뉴스원'}
    if '뉴스1' in names:
        names.append('뉴스일')
    names=list(set(names+[aliases[n] for n in names if n in aliases]))
    issues=[]
    for n,line in [(0,draft.title),*enumerate(draft.script,1)]:
        uncredited = line
        for alias in sorted((allowed or {}).get(n, set()), key=len, reverse=True):
            uncredited = uncredited.replace(alias, '')
        compact=re.sub(r'\s+','',uncredited).casefold()
        # Avoid mistaking ordinary spaced '한국 경제' for the publisher 한국경제.
        exposed=[name for name in names if (name in uncredited if name in {'한국경제','매일경제','서울경제'}
                   else re.sub(r'\s+','',name).casefold() in compact)]
        media_intro=re.search(r'(?:관련\s*소식|이\s*소식|이번\s*소식).*(?:매체|언론|보도|전했)|(?:매체|언론사|언론)\s*(?:보도|보도를|보도에|발표에)|(?:보도를\s*바탕|교차\s*확인|확인\s*출처)',line)
        if exposed or (media_intro and not (allowed or {}).get(n)):
            issues.append(Issue(sentence=n,problem='대본에 내부 매체 출처 노출',
                reason='해당 문장의 검증 사실·의견 근거에 연결되지 않은 매체 출처 안내'+(': '+', '.join(exposed) if exposed else ''),
                direction='해당 매체 원문의 검증 사실 카드와 claims를 정확히 연결하거나 근거 없는 매체 안내를 제거. 근거 있는 언론사 귀속과 실제 발표 기관은 유지'))
        if re.search(r'한국\s*시간|대한민국\s*시간|\bKST\b',line,re.I):
            issues.append(Issue(sentence=n,problem='불필요한 시간대 안내',reason='국내 뉴스 낭독에서 시간대 안내 금지',
                direction='한국시간/KST 안내를 빼고 사건의 날짜·발표일·거래일은 맥락에 맞게 유지'))
        if n and re.search(r'(?:요|죠)[.!?。！？"”’]*$',line.strip()):
            issues.append(Issue(sentence=n,problem='뉴스 문체 불일치',reason='해요/죠 계열의 종결어미가 남음',
                direction='합니다/했습니다/입니다/됩니다 계열로 고치고 40자 제한과 수치·조건을 함께 유지'))
        if n and re.search(r'십월|육월',line):
            issues.append(Issue(sentence=n,problem='월 이름 낭독 오류',reason='6월·10월의 관용 읽기 누락',
                direction='6월은 유월, 10월은 시월로 읽기. 날짜·통계 기준과 원문 인용은 변경하지 않기'))
    first=draft.script[0]
    if re.search(r'브리핑(?:입니다|을|부터)|기준.*(?:뉴스|시사|시장).*(?:입니다|전해)|안녕하세요|첫\s*번째\s*(?:이슈|소식)',first):
        issues.append(Issue(sentence=1,problem='안내형 뉴스 오프닝',reason='첫 문장은 핵심 사건이어야 하며 진행 안내를 낭독하지 않음',
            direction='검증된 발표/행위 주체와 핵심 사건으로 바로 시작. 첫 한두 문장에 실제 날짜·내용을 자연스럽게 연결'))
    return issues
