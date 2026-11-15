"""Explicit v6 opinion evidence and credits, independent of the collector package."""
import json
import re

from .models import Issue

HEADER = '이슈 수집 에이전트 · 원문 인계 v6'
PREFIX = '의견 인용 목록: '
ALIASES = {'KBS': '케이비에스', 'MBC': '엠비씨', 'SBS': '에스비에스',
           'YTN': '와이티엔', 'JTBC': '제이티비씨', 'MBN': '엠비엔',
           'TV조선': '티브이조선', '채널A': '채널에이', '뉴스1': '뉴스원',
           '연합뉴스TV': '연합뉴스티브이'}


def publisher_names(name):
    return {name, ALIASES.get(name, name)} | ({'뉴스일'} if name == '뉴스1' else set())


def names_in_line(name, line):
    return any(alias in line for alias in publisher_names(name))


def parse_opinions(text):
    if not text.startswith(HEADER + '\n'):
        return []
    from .news_context import ORIGINALS_HEADER, ORIGINAL_BLOCK, original_bodies, parse_facts
    original_bodies(text)
    context, _, originals = text.partition('\n' + ORIGINALS_HEADER + '\n')
    lines = [line[len(PREFIX):] for line in context.splitlines() if line.startswith(PREFIX)]
    try:
        if len(lines) != 1:
            raise ValueError
        opinions = json.loads(lines[0])
        articles = {json.loads(m[2])['article_id']: (json.loads(m[2]), m[4])
                    for m in ORIGINAL_BLOCK.finditer(originals)}
        facts = {f['id'] for f in parse_facts(text)}
        if not isinstance(opinions, list) or len(opinions) > 2:
            raise ValueError
        ids, quotes = set(), set()
        keys = {'id', 'article_id', 'attribution', 'speaker', 'quote', 'text', 'mode', 'related_fact_ids', 'reason'}
        for o in opinions:
            if not isinstance(o, dict) or set(o) != keys:
                raise ValueError
            if any(not isinstance(o[k], str) for k in keys - {'related_fact_ids'}):
                raise ValueError
            article, body = articles[o['article_id']]
            if (not re.fullmatch(r'O[0-9]+', o['id']) or o['id'] in ids
                    or (o['article_id'], o['quote']) in quotes
                    or not o['attribution'].strip() or o['attribution'] != article['author']
                    or article.get('official')
                    or (o['speaker'] and o['speaker'] not in body)
                    or not 5 <= len(o['quote']) <= 120 or not o['quote'].strip() or o['quote'] not in body
                    or not 5 <= len(o['text']) <= 100 or not o['reason'].strip()
                    or o['mode'] not in {'direct', 'indirect'}
                    or (o['mode'] == 'direct' and o['text'] != o['quote'])
                    or not isinstance(o['related_fact_ids'], list) or not o['related_fact_ids']
                    or not set(o['related_fact_ids']) <= facts):
                raise ValueError
            ids.add(o['id']); quotes.add((o['article_id'], o['quote']))
        return opinions
    except (KeyError, TypeError, ValueError):
        raise ValueError('invalid_news_opinions') from None


def opinion_links(draft, material, text):
    """Map only explicitly linked opinion cards; a publisher mention is not evidence."""
    opinions = parse_opinions(text)
    from .news_context import ORIGINALS_HEADER, ORIGINAL_BLOCK
    originals = text.partition('\n' + ORIGINALS_HEADER + '\n')[2]
    bodies = {json.loads(m[2])['article_id']: m[4] for m in ORIGINAL_BLOCK.finditer(originals)}
    by_id = {f.id: f for f in material.facts}
    result = {}
    for claim in draft.claims:
        linked = [o for o in opinions if any(
            fid in by_id and o['quote'] in by_id[fid].quote
            and by_id[fid].quote in bodies[o['article_id']]
            and o['id'] in by_id[fid].conditions for fid in claim.fact_ids)]
        if linked:
            result.setdefault(claim.sentence, []).extend(o for o in linked if o not in result.get(claim.sentence, []))
    return result


def speaker_in_line(speaker, line):
    """Keep unnamed institutional speakers intact; allow a named person's role to shorten."""
    if not speaker or speaker in line:
        return True
    name, _, role = speaker.partition(' ')
    # Conservative display alias, not a new identity. Other names retain exact matching.
    if (re.fullmatch(r'[김이박최정강조윤장임한오서신권황안송류유홍전고문양손배백허남심노하곽성차주우구민진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용][가-힣]{2,3}', name)
            and role and re.search(r'총괄|정책관|부원장보|장관|교수|연구원|위원|대표|실장|국장', role)):
        return bool(re.search(r'(?<![가-힣])' + re.escape(name) + r'(?=$|[^가-힣]|은|는|이|가|의)', line))
    return False


def opinion_checks(draft, material, text):
    if not text.startswith(HEADER + '\n'):
        return [], {}
    links = opinion_links(draft, material, text)
    by_id = {f.id: f for f in material.facts}
    issues, allowed = [], {}
    def problem(n, reason):
        issues.append(Issue(sentence=n, problem='의견 인용 근거·귀속 오류', reason=reason,
                            direction='해당 근거 연결·분류·귀속 오류를 수정. 의견을 유지하면 매체·발언 주체도 유지하고, 선택적으로 생략할 때는 의견 내용과 claims를 함께 제거'))
    if len(links) > 2:
        problem(0, '의견 인용은 최대 두 문장으로 제한')
    for claim in draft.claims:
        n = claim.sentence
        if not 1 <= n <= len(draft.script):
            continue
        linked = links.get(n, [])
        is_opinion = claim.kind in {'opinion', 'quotation'} or any(
            fid in by_id and by_id[fid].kind == 'opinion' for fid in claim.fact_ids)
        if not linked:
            if is_opinion:
                problem(n, '승인된 의견 인용 목록과 연결되지 않음')
            continue
        line = draft.script[n - 1]
        # Provenance recognition is separate from narration validity. Do not issue
        # a conflicting publisher-deletion instruction for a repairable quotation.
        allowed[n] = {name for o in linked if names_in_line(o['attribution'], line)
                      for name in publisher_names(o['attribution'])}
        if n == 1:
            problem(n, '첫 문장은 의견이 아닌 검증된 핵심 사실이어야 함')
        if any(by_id[fid].kind != 'opinion' for fid in claim.fact_ids if fid in by_id):
            problem(n, '의견 카드의 kind는 opinion이어야 함')
        if claim.kind not in {'opinion', 'quotation'}:
            problem(n, '의견을 fact/source_statement로 분류함')
        for o in linked:
            credited = names_in_line(o['attribution'], line) and speaker_in_line(o['speaker'], line)
            # One adjacent continuation may share explicit attribution, only for
            # the same set of approved opinions. Never carry it across other facts.
            previous = links.get(n - 1, [])
            if not credited and n > 1 and {x['id'] for x in previous} == {x['id'] for x in linked}:
                prior = draft.script[n - 2]
                credited = names_in_line(o['attribution'], prior) and speaker_in_line(o['speaker'], prior)
            if not credited:
                problem(n, '동일 의견의 해당 문장 또는 바로 앞 인용 문장에 언론사·실제 발언 주체 누락')
            if o['mode'] == 'direct':
                if claim.kind != 'quotation' or not any(q + o['quote'] + end in line for q, end in [('“', '”'), ('"', '"')]):
                    problem(n, '직접 인용의 원문 또는 인용부호 변경')
            elif claim.kind != 'opinion' or any(mark in line for mark in ('“', '”', '"')):
                problem(n, '간접 인용을 직접 인용처럼 표시함')
            # Whether narration preserves stance/uncertainty is semantic review,
            # not a finite verb whitelist (e.g. 지목했습니다 is a valid report).
    return issues, allowed


def fact_publishers(draft, material, text):
    """Recognize credits only when a fact card matches that article's verified evidence."""
    from .news_context import ORIGINAL_HEADERS, ORIGINALS_HEADER, ORIGINAL_BLOCK, original_bodies, parse_facts
    if not text.startswith(ORIGINAL_HEADERS):
        return {}
    original_bodies(text)
    context, _, originals = text.partition('\n' + ORIGINALS_HEADER + '\n')
    articles = {json.loads(m[2])['article_id']: (json.loads(m[2]), m[4])
                for m in ORIGINAL_BLOCK.finditer(originals)}
    evidence = []
    for fact in parse_facts(text):
        prefix = f"근거 {fact['id']}: "
        for line in context.splitlines():
            if line.startswith(prefix):
                evidence.extend(json.loads(line[len(prefix):]))
    by_id = {f.id: f for f in material.facts}
    result = {}
    for claim in draft.claims:
        if claim.kind not in {'fact', 'source_statement'} or not 1 <= claim.sentence <= len(draft.script):
            continue
        line = draft.script[claim.sentence - 1]
        for item in evidence:
            article, body = articles.get(item['article_id'], ({}, ''))
            if article.get('official') or not article.get('author'):
                continue
            linked = any(fid in by_id and by_id[fid].kind == 'source_statement'
                         and by_id[fid].quote.strip() and by_id[fid].quote in body
                         and item['quote'] in body
                         and (by_id[fid].quote in item['quote'] or item['quote'] in by_id[fid].quote)
                         for fid in claim.fact_ids)
            if linked and names_in_line(article['author'], line):
                result.setdefault(claim.sentence, set()).add(article['author'])
    return result


def used_publishers(draft, material, text):
    links = opinion_links(draft, material, text)
    opinions = list(dict.fromkeys(o['attribution'] for n in sorted(links)
        if 1 <= n <= len(draft.script) for o in links[n]
        if names_in_line(o['attribution'], draft.script[n - 1])))
    facts = fact_publishers(draft, material, text)
    return list(dict.fromkeys(opinions + [p for n in sorted(facts) for p in sorted(facts[n])]))


def apply_news_attribution(out):
    text = out['sources'][0]['text']
    from .news_context import ORIGINAL_HEADERS
    if text.startswith(ORIGINAL_HEADERS):
        from .models import Draft, Material
        draft = Draft.model_validate({k: out[k] for k in ('title', 'format', 'tags', 'script', 'claims', 'adaptation_note', 'unresolved')} | {'changes': []})
        out['attribution'] = ', '.join(used_publishers(draft, Material.model_validate(out['material']), text))
    return out
