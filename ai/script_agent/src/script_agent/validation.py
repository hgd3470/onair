import re
import unicodedata
from difflib import SequenceMatcher

from .models import Draft, Issue, Material, SourceMeta
from .rights import resolve_rights
from .news_context import news_checks, original_bodies


MAX_SENTENCE_CHARS = 40
SOURCE_SIMILARITY_THRESHOLD = 0.85
MIN_COPY_CHARS = 15


def source_similarity(line, source_sentences):
    """Editing warning only: ignore spaces/punctuation, not semantic verification."""
    normalize = lambda value: ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())
    candidate = normalize(line)
    if len(candidate) < MIN_COPY_CHARS:
        return 0.0
    best = 0.0
    for sentence in source_sentences:
        source = normalize(sentence)
        if len(source) < MIN_COPY_CHARS:
            continue
        if candidate in source:
            return 1.0
        # Bound comparisons to similar-length sentences. Substring copies above
        # are still caught when a long source sentence was split into short lines.
        if min(len(source), len(candidate)) / max(len(source), len(candidate)) < 0.7:
            continue
        best = max(best, SequenceMatcher(None, candidate, source, autojunk=False).ratio())
    return best


def validate_material(material: Material, text: str, meta: SourceMeta):
    ids = [f.id for f in material.facts]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r'f[1-9][0-9]*', i) for i in ids):
        raise ValueError('invalid_fact_ids')
    if any(not f.quote.strip() or f.quote not in text for f in material.facts):
        raise ValueError('fact_quote_not_in_source')
    originals = original_bodies(text)
    if originals is not None and any(not any(f.quote in body for body in originals) for f in material.facts):
        raise ValueError('fact_quote_not_in_original_article')
    if material.category not in {f'C{i:02}' for i in range(1, 22)}:
        raise ValueError('invalid_category')
    if meta.category and material.category != meta.category:
        raise ValueError('category_override')
    if meta.production_type and material.production_type != meta.production_type:
        raise ValueError('type_override')


def script_char_count(draft, mode='non_whitespace'):
    if mode == 'with_spaces':
        return len(unicodedata.normalize('NFC', ' '.join(draft.script)))
    if mode != 'non_whitespace':
        raise ValueError('invalid_character_count_mode')
    return len(re.sub(r'\s', '', ''.join(draft.script)))


def estimated_duration(draft, cps=4.5, mode='non_whitespace'):
    if cps <= 0:
        raise ValueError('invalid_chars_per_sec')
    return round(script_char_count(draft, mode) / cps, 1)


def checks(draft: Draft, material: Material, text: str, meta: SourceMeta, cps=4.5, count_mode='non_whitespace', *, min_sec=None, max_sec=None):
    problems = news_checks(draft, material, text)
    def add(n, problem, reason, direction):
        problems.append(Issue(sentence=n, problem=problem, reason=reason, direction=direction))
    claims = {c.sentence: c for c in draft.claims}
    if len(claims) != len(draft.claims) or set(claims) != set(range(1, len(draft.script)+1)):
        add(0, '문장 근거표 누락/중복', '각 문장에는 근거표 한 항목이 필요함', '문장 번호를 1부터 빠짐없이 배정')
    facts = {f.id for f in material.facts}
    for c in draft.claims:
        if set(c.fact_ids) - facts:
            add(c.sentence, '없는 사실 ID', '사실 카드에 없는 ID 참조', '제공된 사실 ID만 사용')
        if c.kind in {'fact', 'source_statement', 'quotation'} and not c.fact_ids:
            add(c.sentence, '근거 없는 주장', '사실/인용은 원문 카드 필요', '근거를 연결하거나 주장 제거')
    source_sentences = re.split(r'(?<=[.!?。？！])\s+|\n+', text)
    for n, line in enumerate(draft.script, 1):
        normalized_line = unicodedata.normalize('NFC', line)
        if len(normalized_line) > MAX_SENTENCE_CHARS:
            add(n, '문장 40자 초과', f'공백·문장부호 포함 {len(normalized_line)}자 / 상한 40자',
                '조건·발화 주체를 유지하며 짧은 완결 문장으로 나누고 claims 번호 갱신')
        if re.search(r'\d|[a-zA-Z]', unicodedata.normalize('NFKC', line)):
            add(n, '숫자·영문 한글 읽기 필요', '낭독문에 숫자 또는 영문 표기가 남음',
                '문맥에 맞게 한글로 읽어 쓰기: 20메가와트→이십 메가와트, 9급→구 급, AI→에이아이; 수치·단위 보존')
        if material.production_type != '원문낭독형':
            similarity = source_similarity(line, source_sentences)
            if similarity >= SOURCE_SIMILARITY_THRESHOLD:
                add(n, '원문 표현 재서술 필요', f'표면 유사도 {similarity:.0%}; 편집 경고이며 권리 판단 아님',
                    '핵심 사실·조건은 유지하고 설명 구조와 문장을 새로 작성; 단어 치환·문장 분할만으로 회피하지 않기')
        if not line.strip() or '\n' in line or re.search(r'https?://|^#{1,6}\s|^\[.*\]$', line):
            add(n, '낭독문 형식', '공백/줄바꿈/URL/연출 지시가 본문에 있음', '한 원소에 낭독 문장 하나만 작성')
        # A full stop inside a decimal is not a sentence break.
        if re.search(r'[.!?。？！][”"\'’]?\s+\S', line):
            add(n, '한 줄에 여러 문장', '문장 구분 기호 뒤에 다음 문장이 있음', '배열 원소를 문장별로 나누고 근거표 번호 갱신')
    if material.production_type == '원문낭독형':
        quote_lines = [draft.script[c.sentence-1] for c in draft.claims
                       if c.kind == 'quotation' and 1 <= c.sentence <= len(draft.script)]
        normalized = lambda s: re.sub(r'\s+', '', s)
        if normalized(''.join(quote_lines)) != normalized(meta.reading_text or text):
            add(0, '원문 불일치', '지정 낭독 구간과 인용 문장이 다름', '원문 글자와 문장부호를 보존하고 quotation으로 표시')
    if material.production_type != '원문낭독형':
        sec = estimated_duration(draft, cps, count_mode)
        if (min_sec is not None and sec < min_sec) or (max_sec is not None and sec > max_sec):
            add(0, '재생 분량 절대 범위 이탈', f'보정 추정 {sec}초 / 허용 {min_sec}~{max_sec}초, 음성 실측 아님',
                '범위를 지키되 사실·조건을 보존하고 중복이나 부수 설명을 조정')
        if not meta.target_sec * 0.7 <= sec <= meta.target_sec * 1.3:
            add(0, '목표 분량 이탈', f'문자수 휴리스틱 {sec}초 / 목표 {meta.target_sec}초, 실측 아님', '조건과 사건을 유지하며 목표 문자수에 맞춰 조정')
    return problems


def rights_status(meta):
    return resolve_rights(meta)[2]


def align_material_quotes(material: Material, text: str):
    """Recover a unique literal source span after formatting-only LLM changes.

    Never accept a normalized quote as evidence: replace it with the actual source
    slice, retain an audit record, then run the ordinary exact-substring validator.
    Numbers, word order, punctuation and verse/footnote markers are not removed.
    """
    def canonical(value):
        skipped = set()
        # Word-only Markdown emphasis; do not strip math, underscores or symbols.
        pattern = r'(?<![\w*])(\*{1,2})([^\W\d_]+(?:[ -][^\W\d_]+)*)\1(?![\w*])'
        for match in re.finditer(pattern, value):
            if sum(c.isalpha() for c in match.group(2)) < 3:
                continue
            width = len(match.group(1))
            skipped.update(range(match.start(), match.start() + width))
            skipped.update(range(match.end() - width, match.end()))
        chars, positions = [], []
        for index, char in enumerate(value):
            if index in skipped:
                continue
            if char.isspace():
                if chars and chars[-1] == ' ':
                    continue
                char = ' '
            chars.append(char)
            positions.append(index)
        return ''.join(chars), positions

    fixed = material.model_copy(deep=True)
    haystack, offsets = canonical(text)
    notes = []
    for fact in fixed.facts:
        if fact.quote in text:
            continue
        needle, _ = canonical(fact.quote)
        if len(needle) < 20:
            continue
        start = haystack.find(needle)
        if start < 0 or haystack.find(needle, start + 1) >= 0:
            continue
        left, right = offsets[start], offsets[start + len(needle) - 1] + 1
        exact = text[left:right]
        notes.append({'fact_id': fact.id, 'model_quote': fact.quote, 'source_quote': exact,
                      'source_start': left, 'source_end': right,
                      'method': 'unique_source_span_markdown_emphasis_and_whitespace'})
        fact.quote = exact
    return fixed, notes
