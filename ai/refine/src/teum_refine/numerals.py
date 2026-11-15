"""Conservative Korean numerals with exact Decimal values and percentage units."""
import re
from decimal import Decimal,InvalidOperation
DIGITS=dict(zip('영공일이삼사오육칠팔구',[0,0,1,2,3,4,5,6,7,8,9]))
SMALL={'십':10,'백':100,'천':1000};BIG={'만':10000,'억':100000000,'조':1000000000000}
ONES={'하나':1,'한':1,'둘':2,'두':2,'셋':3,'세':3,'넷':4,'네':4,'다섯':5,'여섯':6,'일곱':7,'여덟':8,'아홉':9}
TENS={'열':10,'스물':20,'스무':20,'서른':30,'마흔':40,'쉰':50,'예순':60,'일흔':70,'여든':80,'아흔':90}
NATIVE={**ONES,**TENS,**{a+b:v+w for a,v in TENS.items() if a!='스무' for b,w in ONES.items()}}
UNIT_ALIASES={'달':'개월','해':'년','시':'시간','원화':'원','살':'세','회':'번','cm':'센티미터','centimeter':'센티미터','centimeters':'센티미터','km':'킬로미터','kilometer':'킬로미터','kilometers':'킬로미터','kg':'킬로그램','kilogram':'킬로그램','kilograms':'킬로그램','min':'분','minute':'분','minutes':'분'}
SINO='영공일이삼사오육칠팔구십백천만억조'
UNIT=r'퍼센트\s*포인트|퍼센트|%\s*포인트|%p|%|달러|개월|시간|킬로미터|킬로그램|포인트|톤|년|해|월|일|달|살|세|점(?!\s+[영공일이삼사오육칠팔구\d]+(?=$|\s))|개|명|자|번|회|시|분|초|원화|원|쪽|권|대|짝|켤레|배|층|미터|센티미터|(?:centimeters?|cm|kilometers?|km|kilograms?|kg|minutes?|min)(?![A-Za-z])'
BODY=rf'[\d{SINO},]+(?:\.\d+)?(?:\s+[\d{SINO},]+(?:\.\d+)?)*(?:\s*점\s*[영공일이삼사오육칠팔구\d]+)?'
NATIVE_RE='|'.join(sorted(NATIVE,key=len,reverse=True))
PATTERN=re.compile(rf'(?<![가-힣\w])(?:(?P<body>유(?=월)|시(?=월)|{NATIVE_RE}|{BODY})\s*(?:여\s*)?(?P<unit>{UNIT})(?=$|[^가-힣]|은|는|이|가|을|를|에|의|으로|보다|씩|짜리|였|간|물|째|까지|부터|도|과|와|만|쯤|정도|마다|가량|입니다|인)|(?P<bare>{BODY}))')
def parse_number(word):
 word=re.sub(r'\s+|,','',word)
 if word in NATIVE:return Decimal(NATIVE[word])
 if '점' in word:
  if word.count('점')!=1:return None
  left,right=word.split('점');base=parse_number(left)
  if base is None or not right or any(c not in DIGITS and not c.isdigit() for c in right):return None
  frac=''.join(str(DIGITS[c]) if c in DIGITS else c for c in right)
  return base+Decimal('0.'+frac)
 if re.fullmatch(r'\d+(?:\.\d+)?',word):return Decimal(word)
 tokens=re.findall(r'\d+(?:\.\d+)?|[영공일이삼사오육칠팔구십백천만억조]',word)
 if ''.join(tokens)!=word:return None
 total=Decimal(0);section=Decimal(0);pending=None;last_small=10000;last_big=10**16
 for t in tokens:
  if t in BIG:
   unit=BIG[t]
   if unit>=last_big:return None
   amount=section+(pending if pending is not None else 0)
   total+=(amount or 1)*unit;section=Decimal(0);pending=None;last_small=10000;last_big=unit
  elif t in SMALL:
   unit=SMALL[t]
   if unit>=last_small:return None
   section+=(pending if pending is not None else 1)*unit;pending=None;last_small=unit
  else:
   if pending is not None:return None # Ambiguous digit-word sequences outside decimals.
   pending=Decimal(DIGITS[t]) if t in DIGITS else Decimal(t)
 return total+section+(pending if pending is not None else 0)
def extract(text):
 out=[]
 for m in PATTERN.finditer(str(text)):
  body=m['body'] or m['bare'];start=m.start();unit=re.sub(r'\s+','',m['unit'] or '');tail=str(text)[m.end():]
  if tail and re.match('[가-힣]',tail) and not re.match(r'(?:은|는|이|가|을|를|에|의|으로|보다|씩|짜리|였|간|물|째|까지|부터|도|과|와|만|쯤|정도|마다|가량|입니다|인)',tail):continue
  # A separated demonstrative is not a leading digit; 만 before age means full age.
  prefix=re.match(r'^이\s+(?=[백천\d])',body)
  if unit in ('살','세') and not prefix:prefix=re.match(r'^만\s+(?=\d)',body)
  if prefix:
   start+=prefix.end();body=body[prefix.end():]
  if unit=='배' and body.strip()=='이' and re.match(r'(?:는|가|를|의|에)',tail):continue
  if unit=='번' and re.match(r'\s*째',tail):continue
  if '점' in body and not unit and not re.search(r'\s점\s',body):continue # 이점이 is a noun, not 2.2.
  if body.replace(' ','')=='구조':continue # A common noun, not nine trillion.
  # Require a counter (or a decimal/grouping unit), and do not split
  # ordinary attached words such as 이자 or 십분. Compact dates are retained.
  if not re.search(r'\d',body):
   if not unit and '점' not in body and (len(body.strip())<2 or not body.rstrip().endswith(('만','억','조'))):continue
   if unit and not body.endswith((' ', '\t', '\n')) and not re.search(r'\s',m.group()[len(body):len(m.group())-len(m['unit'])]):
    if unit not in ('년','월','일') and '점' not in body:continue
  if not unit and tail and re.match('[가-힣]',tail):continue
  if unit=='일' and body.strip()=='이' and not re.search(r'월\s*$',str(text)[:m.start()]):continue # '이 일' can mean 'this work'.
  if unit in ('월','일') and re.sub(r'\s+','',body) in NATIVE:continue
  value=Decimal(6 if body=='유' else 10) if unit=='월' and body in ('유','시') else parse_number(body)
  if value is None:continue
  kind='pp' if unit in ('퍼센트포인트','%포인트','%p') else 'pct' if unit in ('퍼센트','%') else UNIT_ALIASES.get(unit,unit)
  out.append(dict(value=value,unit=kind,suffix=unit,text=str(text)[start:m.end()],start=start,end=m.end()))
 return out
def normalize(text):
 text=str(text)
 for x in reversed(extract(text)):
  # Compact units allow the existing calendar parser to recognize Hangul dates.
  text=text[:x['start']]+format(x['value'],'f')+x['suffix']+text[x['end']:]
 return text
def evidence_tokens(d):
 from .input import checkable_facts
 material=d['material'];out=[]
 def unit_kind(unit):
  return 'pp' if unit in ('pp','%p','%포인트','퍼센트포인트','percentpoint') else 'pct' if unit in ('%','퍼센트','percent','pct') else UNIT_ALIASES.get(unit,str(unit or ''))
 def scalar(value,unit=''):
  if value is None or isinstance(value,bool):return
  for token in extract(str(value)):
   # Signed changes denote rise/fall; the spoken magnitude remains positive.
   if unit:token={**token,'unit':unit}
   out.append(token)
 for fact in checkable_facts(d):
  if not isinstance(fact,dict):out.extend(extract(str(fact)));continue
  for key in ('text','quote','label'):
   if fact.get(key):out.extend(extract(str(fact[key])))
  unit=unit_kind(fact.get('unit'))
  for key in ('value','prev','change','change_pct','change_pp','%포인트'):
   if key in fact:scalar(fact[key],'pct' if key=='change_pct' else 'pp' if key in ('change_pp','%포인트') or key=='change' and unit=='pct' else unit)
 for text in [material.get('as_of',''),material.get('info_as_of','')]:out.extend(extract(str(text)))
 for source in d['sources']:
  for text in (source.get('info_as_of',''),source.get('_text','')):out.extend(extract(str(text)))
 return out
def unmatched(text,allowed):
 return [x for x in extract(text) if not any(x['value']==a['value'] and (not x['unit'] or x['unit']==a['unit'] or x['unit']=='점' and not a['unit']) for a in allowed)]
