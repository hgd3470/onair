"""Deterministic v0 checks; all sentence references are one-based."""
import re
from .input import is_economy,checkable_facts
from .numerals import normalize,evidence_tokens,unmatched
from .legacy_rules import grams, lcs_len, numbers_in, allowed_numbers, HARM_WORDS, ADVICE_WORDS

def rule_fix(stage,reason):
    actions=[]
    for trigger,action in [('숫자·영문','숫자와 영문을 한글 읽기로 바꾸기'),('음절 >','두 문장으로 나누기'),('괄호','괄호 없이 풀어 쓰기'),('설명 없는 영문','영문을 한글로 읽고 뜻을 바로 풀어 쓰기'),('긴 숫자열','숫자를 한글로 읽고 문장을 나누기'),('개 나열','숫자 정보를 여러 문장으로 나누기'),('TTS 오독','기호를 한글 읽기로 풀어 쓰기'),('원문 중복','원문 표현을 쓰지 않고 다시 쓰기'),('250~450','250~450자로 맞추기')]:
        if trigger in reason:actions.append(action)
    if actions:return '; '.join(dict.fromkeys(actions))
    if '원천 고유 요소 잔존: ' in reason:return reason.split(': ',1)[1]+' 표현을 일반화하거나 빼기'
    if stage=='R1':return '원천 이용 조건 확인 후 다시 제출'
    if stage=='R3-a':return '근거의 수치·단위·날짜와 대조해 정확히 고치기'
    if stage=='R3':return '원천 근거와 조건을 확인하고 주장을 고치기'
    if stage=='R4c':return '투자 권유나 수익 보장 표현을 빼고 사람 검수 받기'
    if stage in ('R4','R4-a'):return '위험·유해 표현을 빼고 안전한 설명으로 바꾸기'
    if stage=='R2':return '원천 표현과 고유 정보를 일반화해 다시 쓰기'
    if stage=='R5a':return '한 문장에 한 정보만 담아 쉽게 다시 쓰기'
    return '입력 또는 연결 오류를 해결한 뒤 다시 검수 요청'

NON_FIXABLE={'R0','R1','R4-a','R4c'}
def normalize_item(item):
    item=dict(item);sent=item.get('sent',item.get('sentence'))
    sent=sent if type(sent) is int and sent>0 else None
    item.update(sent=sent,sentence=sent,scope='sentence' if sent is not None else 'script')
    item['fixable']=item.get('stage') not in NON_FIXABLE and bool(item.get('fixable',True))
    item['fix']=item.get('fix') or rule_fix(item.get('stage',''),item.get('reason',''))
    item['direction']=item['fix']
    return item

def issue(stage, decision, reason, sent=None, fix=None, fixable=True):
    return normalize_item(dict(stage=stage,decision=decision,sent=sent,problem=reason,reason=reason,fix=fix,fixable=fixable))

def combine(items, feedback_revision=0):
    levels = {x['decision'] for x in items}
    for level in ('reject', 'human_review', 'revise', 'pass_with_flags'):
        if level in levels:
            return 'human_review' if level == 'revise' and feedback_revision >= 2 else level
    return 'pass'

def license_checks(d):
    out=[]
    typ=d['production_type']
    for s in d['sources']:
        if 'site_grade' in s:
            from .site import GRADES
            level,reason=GRADES.get(s['site_grade'],('human_review','원천 ID 또는 이용조건 미확인'))
            if level!='pass':out.append(issue('R1',level,s['source_id']+': '+reason+(' / '+s.get('attribution','') if level=='pass_with_flags' else '')))
            continue
        lic=s['license']; kind=str(lic.get('kind') or 'unknown').lower(); commercial=lic.get('commercial')
        if typ=='사실해설' and s.get('source_kind')=='user_summary' and s.get('original_sources'):
            # Issue-collector news handoff: articles are used for facts only, script is rewritten (team policy 2026-11-13).
            out.append(issue('R1','pass_with_flags',s['source_id']+': 원기사 사실 이용(팀 방침), 매체 표기 확인'))
            if not s.get('attribution'):out.append(issue('R1','pass_with_flags','출처 표시 필요'))
            continue
        restricted=lambda value: value is False or isinstance(value,str) and value.strip().lower() in ('no','false')
        if kind=='internal-test-fixture' and d.get('fixture') is True:
            out.append(issue('R1','pass_with_flags','테스트용 원천'));continue
        if kind in ('unknown','미확인') or any(k in lic and (lic[k] is None or str(lic[k]).lower()=='unknown') for k in ('commercial','modify','share_alike')):
            if s.get('usage') != 'theme-only': out.append(issue('R1','human_review',s['source_id']+': 외부 원천 권리 미확인'))
        if restricted(commercial) or any(x in kind for x in ('공공누리 2','공공누리 3','공공누리 4')):
            out.append(issue('R1','reject',s['source_id']+': 상업 이용 불가 또는 미확인'))
        elif commercial == 'non-commercial-only':
            out.append(issue('R1','pass_with_flags' if typ in ('사실해설','사실서사') else 'reject','비상업 데모 전용'))
        if restricted(lic.get('modify')) and typ!='원문낭독':
            out.append(issue('R1','reject','변경 불가'))
        if lic.get('share_alike') in (True,'yes') or 'by-sa' in kind:
            out.append(issue('R1','human_review' if typ in ('창작·각색','해석·감상') else 'pass_with_flags','BY-SA 조건 확인 및 동일조건 표시'))
        if not s.get('attribution'):
            out.append(issue('R1','pass_with_flags','출처 표시 필요'))
    return out

def check(d, syllables_per_sec=6.1):
    if syllables_per_sec<=0: raise ValueError("syllables_per_sec must be positive")
    out=license_checks(d)+listening_checks(d['script'])+date_checks(d); typ=d['production_type']; material=d['material']
    allowed=evidence_tokens(d)
    narrative_conflict=d.get('schema_version')=='1.0.0' and isinstance(material.get('conflict'),str)
    if material.get('conflict') and not narrative_conflict: out.append(issue('R3','human_review','소재 사실 충돌'))
    for n,sent in enumerate(d['script'],1):
        if checkable_facts(d) or any(x.get('_text','').strip() for x in d['sources']):
            bad=[x['text'] for x in unmatched(strip_dates(normalize(sent)),allowed)]
            if bad: out.append(issue('R3-a','pass_with_flags' if typ=='창작·각색' else 'revise',f'사실 카드에 없는 수치: {bad}',n))
        for w in material.get('must_not_use',[]):
            if w in sent: out.append(issue('R2','revise','원천 고유 요소 잔존: '+w,n))
        for w in HARM_WORDS:
            if w in sent: out.append(issue('R4','human_review','유해 표현: '+w,n))
        for w in ADVICE_WORDS:
            if w in sent:
                if is_economy(d):out.append({**issue('R4c','human_review','전문 권유: '+w,n),'category':'invest'})
                else:out.append(issue('R4','pass_with_flags','전문 권유: '+w,n))
        if re.search(r'(화면.{0,8}(보세요|확인하세요)|검색하세요|눈을? 감|명상하세요|운전 중.{0,15}(조작|확인))',sent):
            out.append(issue('R4','revise','운전 중 위험 동작 지시',n))
        quoted=typ=='원문낭독' and any(x.get('kind')=='quotation' and x.get('sentence')==n for x in d.get('claims',[]))
        if quoted and not any(sent in x.get('_text','') for x in d['sources']):
            out.append(issue('R2','revise','인용 문장이 원문과 정확히 일치하지 않음',n,'원문 인용을 정확히 복원'))
        for src in d['sources']:
            text=src.get('_text','')
            if not text: continue
            length,span=overlap_span(sent,text,copy_names(d,src))
            if typ!='원문낭독' and length>=10:
                permitted=any(x in src['license']['kind'].lower() for x in ('pd','by-sa','cc0'))
                out.append(issue('R2','revise' if src.get('usage')=='facts-only' else 'pass_with_flags' if permitted or is_economy(d) else 'revise',f'원문 중복 lcs={length}',n,'이 구절을 바꿔 쓰기: '+span))
    chars=sum(len(re.sub(r'\s','',x)) for x in d['script'])
    if not 250<=chars<=450:out.append(issue('R5a','pass_with_flags',f'본문 {chars}자: 권장 250~450자 밖'))
    return merge_r2(out)


def strip_dates(text):
    # Calendar/period expressions are left to R3; financial amounts remain checked.
    text=re.sub(r'(?<!\d)\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?', ' ', text)
    return re.sub(r'\d+(?:\.\d+)?\s*(?:년|개월|월|주일|주|일)(?:간)?', ' ', text)

def kanana_items(token):
    if token=='<SAFE>': return []
    if re.fullmatch(r'<UNSAFE-S[1-7]>',token):
        return [issue('R4-a','human_review','Kanana: '+token)]
    raise ValueError('unexpected Kanana output: '+token)

# In-sample calibration on the 37 clean scripts: 468 sentences, 3 exceed 43.
LISTEN_MAX_SYLLABLES=43
LISTEN_NUMBER_COUNT=3
NUMBER_RE=re.compile(r'\d+(?:[,.]\d+)*')

def listening_checks(script):
    out=[]
    for i,s in enumerate(script):
        reasons=[]
        if re.search(r'[0-9A-Za-z]',s):reasons.append('숫자·영문은 한글 읽기로')
        next_sent=script[i+1] if i+1<len(script) else ''
        if re.search(r'[()\[\]{}（）［］【】]',s):reasons.append('괄호/대괄호')
        for m in re.finditer(r'[A-Za-z]+(?:[-.][A-Za-z]+)*',s):
            # Explicit following Korean gloss, not merely any Korean in the sentence.
            gloss=r'^\s*(?:[（(]\s*[가-힣][가-힣\s]{0,30}[)）]|(?:즉|다시 말해|곧)\s+[가-힣]|[가-힣][가-힣\s]{0,25}(?:라는 뜻|의 약자|을 뜻|를 뜻))'
            next_gloss=r'^\s*(?:즉|이는|이것은|다시 말해)\s*[가-힣].*(?:뜻|말|의미)'
            if not re.search(gloss,s[m.end():]) and not re.search(next_gloss,next_sent):reasons.append('설명 없는 영문: '+m.group())
        nums=NUMBER_RE.findall(s)
        if any(len(re.sub(r'\D','',v))>=5 or '.' in v for v in nums):reasons.append('긴 숫자열/소수')
        if len(nums)>=LISTEN_NUMBER_COUNT:reasons.append(f'숫자 {len(nums)}개 나열')
        if re.search(r'[%‰&@#=+*/<>≤≥±×÷₩$€^_\\|~→←]',s):reasons.append('TTS 오독 가능 기호')
        syllables=len(re.findall('[가-힣]',s))
        if syllables>LISTEN_MAX_SYLLABLES:reasons.append(f'한글 {syllables}음절 > {LISTEN_MAX_SYLLABLES}')
        if reasons:out.append(issue('R5a','revise','규칙: '+', '.join(dict.fromkeys(reasons)),i+1))
    return out

DATE_RE=re.compile(r'(?<!\d)(?:(?P<iy>\d{4})[-/.](?P<im>\d{1,2})[-/.](?P<id>\d{1,2})|(?P<y>\d{4})년(?:\s*(?P<ym>\d{1,2})월(?:\s*(?P<yd>\d{1,2})일)?)?|(?P<m>\d{1,2})월(?:\s*(?P<d>\d{1,2})일)?)')
def dates_in(text):
    dates=[]
    for m in DATE_RE.finditer(normalize(text)):
        g=m.groupdict()
        dates.append((tuple(int(v) if v is not None else None for v in (g['iy'] or g['y'],g['im'] or g['ym'] or g['m'],g['id'] or g['yd'] or g['d'])),m.group()))
    return dates

def date_checks(d):
    import json
    material=d['material']
    refs=dates_in(json.dumps(checkable_facts(d),ensure_ascii=False))
    for x in [*[s.get('_text','') for s in d['sources']],material.get('as_of',''),material.get('info_as_of',''),*[s.get('info_as_of','') for s in d['sources']]]:refs+=dates_in(str(x))
    briefing=None
    for s in d['script']:
        ds=dates_in(s)
        if ds and re.search(r'브리핑|오늘은|오늘 날짜|기준일',s):briefing=ds[0][0];refs.append(ds[0]);break
    default_year=next((v[0][0] for v in refs if v[0][0]),2000)
    out=[]
    for n,s in enumerate(d['script'],1):
        for date,text in dates_in(s):
            y,m,day=date
            valid=(m is None or 1<=m<=12) and (day is None or 1<=day<=31)
            matches=any(all(a is None or a==(b if b is not None else (default_year if i==0 else None)) for i,(a,b) in enumerate(zip(date,r))) for r,_ in refs)
            later=bool(briefing) and (y or default_year,m or 1,day or 1)>(briefing[0] or default_year,briefing[1] or 1,briefing[2] or 1)
            if not valid or not matches or later:out.append(issue('R3-a','revise',f'날짜 {text}: '+('브리핑 날짜보다 이후' if later else '허용된 근거 날짜와 불일치'),n))
    return out


def merge_r2(items):
    """One fixable R2 item per sentence; a warning never becomes a required fix."""
    out=[];groups={};rank={'pass_with_flags':0,'revise':1,'human_review':2,'reject':3}
    for item in items:
        if item.get('stage')!='R2' or not item.get('sent') or not item.get('fixable',True):
            out.append(item);continue
        sent=item['sent']
        if sent not in groups:
            groups[sent]=len(out);out.append(dict(item));continue
        old=out[groups[sent]];old_rank=rank[old['decision']];new_rank=rank[item['decision']]
        old_reason='참고 경고: '+old['reason'] if old_rank<new_rank else old['reason']
        new_reason='참고 경고: '+item['reason'] if new_rank<old_rank else item['reason']
        reasons=list(dict.fromkeys([old_reason,new_reason]))
        fixes=[old['fix'],item['fix']] if old_rank==new_rank else [item['fix'] if new_rank>old_rank else old['fix']]
        old.update(reason='; '.join(reasons),problem='; '.join(reasons),fix='; '.join(dict.fromkeys(fixes)),decision=max((old['decision'],item['decision']),key=rank.get))
        old['direction']=old['fix']
    return out


def copy_names(d,source):
    """Explicit entity/title metadata plus unambiguous written name markers.

    Do not guess every Korean noun is a name: that would erase copied prose.
    """
    names=set()
    for owner in (d,d.get('material',{}),source):
        for key in ('title','name','proper_nouns','entities'):
            value=owner.get(key,[])
            for entry in value if isinstance(value,list) else [value]:
                if isinstance(entry,dict):entry=entry.get('text') or entry.get('name')
                if isinstance(entry,str) and entry.strip():names.add(entry.strip())
    text=source.get('_text','')+'\n'+'\n'.join(d.get('script',[]))
    names.update(re.findall(r'^#{1,6}\s+(.+)$',text,re.M))
    names.update(re.findall(r'[《〈「『]([^》〉」』\n]+)[》〉」』]',text))
    # Station names and their named facilities, organizations, and titled people.
    for pattern in (
        r'(?<![가-힣])([가-힣]{2,12}역)(?=[은는이가의를에\s,.]|$)',
        r'(?<![가-힣])([가-힣]{2,16}(?:학회|재단|대학교|주식회사|국립공원))(?=[은는이가의를에\s,.]|$)',
        r'(?<![가-힣])([김이박최정강조윤장임한오서신권황안송류홍][가-힣]{0,2}\s*(?:부장|과장|대리|교수|박사|대통령)(?:님)?)(?=[은는이가의를\s,.]|$)',
    ):names.update(re.findall(pattern,text))
    return names


def copy_segments(text,names):
    """Mask spans as boundaries, retaining offsets for actionable overlap text."""
    from .numerals import extract
    from .legacy_rules import norm
    spans=[(x['start'],x['end']) for x in extract(text)]
    patterns=[r'[《〈「『][^》〉」』\n]{1,20}[》〉」』]',  # short bracketed titles only; quoted sentences stay comparable
             r'\d+(?:[.,:/-]\d+)*',r'(?<![가-힣])(?:사흘|이틀|나흘|며칠)(?:째|간)?']
    for pattern in patterns:
        spans.extend(m.span() for m in re.finditer(pattern,text))
    for name in names:
        spans.extend(m.span() for m in re.finditer(re.escape(name),text))
    mask=[False]*len(text)
    for start,end in spans:mask[start:end]=[True]*(end-start)
    segments=[];chars=[];offsets=[]
    for i,char in enumerate(text):
        if mask[i]:
            if chars:segments.append((''.join(chars),offsets));chars=[];offsets=[]
        elif norm(char):chars.append(char);offsets.append(i)
    if chars:segments.append((''.join(chars),offsets))
    return segments


def overlap_span(sentence,source,names=()):
    from difflib import SequenceMatcher
    best=0;span=''
    source_parts=copy_segments(source,names)
    for part,offsets in copy_segments(sentence,names):
        for other,_ in source_parts:
            if min(len(part),len(other))<=best:continue
            match=SequenceMatcher(None,part,other,autojunk=False).find_longest_match()
            if match.size>best:
                best=match.size;span=sentence[offsets[match.a]:offsets[match.a+best-1]+1]
    return best,span
