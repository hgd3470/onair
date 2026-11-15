"""Offline admin-site CSV adapter. Does not upload to the site."""
import csv,re
from pathlib import Path
from .input import source_metadata,write
GRADES={'자유 이용':('pass','자유 이용'), '조건부(표시)':('pass_with_flags','출처·이용조건 표시 확인'), '조건부(동일조건)':('pass_with_flags','BY-SA 동일조건 표시 확인'), '조건부(건별)':('human_review','건별 이용 허락 확인'), '사실만 참고':('pass_with_flags','사실만 참고: 표현 복사 금지'), '허락 전 금지':('reject','허락 전 이용 금지')}
def note_sources(note,catalog):
 wrapped=note if re.search(r'## [34]\.',note) else '## 4. 출처와 작성 범위\n'+note
 linked=set(re.findall(r'\]\((https?://[^)]+)\)',wrapped))
 for url in dict.fromkeys(re.findall(r'https?://[^\s)<>]+',note)):
  url=url.rstrip('.,;')
  if url not in linked:wrapped+='\n[출처]('+url+')'
 fallback,details=source_metadata(wrapped)
 ids=list(dict.fromkeys(re.findall(r'SRC-\d+',note)))
 if not ids:return fallback,details
 sources=[]
 for sid in ids:
  row=catalog.get(sid,{})
  sources.append(dict(source_id=sid,source_url=row.get('url',''),provider=row.get('name',''),attribution=row.get('credit',''),declared_note=note,site_grade=row.get('grade','unknown'),usage='facts-only' if row.get('grade')=='사실만 참고' else 'basis',license=dict(kind='site-grade',commercial='yes' if row.get('grade') in GRADES else 'unknown',modify='yes',share_alike=False)))
 # Keep unmatched declared sources rather than losing unknown rights.
 from urllib.parse import urlsplit
 for extra in fallback:
  u=urlsplit(extra.get('source_url',''))
  if not any(u.netloc==urlsplit(x.get('source_url','')).netloc and u.path.startswith(urlsplit(x.get('source_url','')).path.rstrip('/')) for x in sources):sources.append(extra)
 return sources,details
def convert_kit(kit,out):
 kit=Path(kit)
 with (kit/'원천목록.csv').open(encoding='utf-8-sig',newline='') as f:catalog={r['id']:r for r in csv.DictReader(f)}
 with (kit/'검수대상.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
 seen=set()
 for row in rows:
  for k in ('id','title','format','body','source_note'):
   if k not in row:raise ValueError('site missing '+k)
  if not re.fullmatch(r'[A-Za-z0-9_-]+',row['id']):raise ValueError('invalid site id')
  filename=row['id'];suffix=2
  while filename in seen:filename=row['id']+'__'+str(suffix);suffix+=1
  seen.add(filename)
  fmt=row['format'];typ='원문낭독' if '낭독' in fmt else '창작·각색' if '창작' in fmt else '사실서사' if '사실 기반' in fmt else '사실해설'
  sources,details=note_sources(row['source_note'],catalog)
  write(dict(content_id=row['id'],title=row['title'],format=fmt,category='story' if typ=='창작·각색' else 'life',production_type=typ,script=[s.strip() for s in row['body'].splitlines() if s.strip()],sources=sources,material={'details':details},generator={'revision':0},site_source_note=row['source_note']),out,filename=filename)
 return Path(out)
def export_results(results,out):
 from .rules import normalize_item,rule_fix
 def describe(x):
  x=normalize_item(x)
  direction=x.get('direction','').strip()
  suffix=' → '+direction if direction and direction!=rule_fix(x.get('stage',''),'') else ''
  return ('대본 전체: ' if x['scope']=='script' else f"{x['sent']}번 문장: ")+x['reason']+suffix
 with (Path(out)/'site_result.csv').open('w',encoding='utf-8-sig',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=['id','판정','사유']);writer.writeheader()
  for r in results:
   decision=r['decision'];items=r['items']
   if decision in ('pass','pass_with_flags'):
    verdict='통과';reason='; '.join(describe(x) for x in items if x['decision']=='pass_with_flags')
   elif decision=='revise':
    verdict='수정 필요';reason='; '.join(describe(x) for x in [x for x in items if x['decision']=='revise'][:3])
   else:
    verdict='';reason='사람 확인 필요: '+'; '.join(describe(x) for x in items if x['decision'] in ('human_review','reject'))
   writer.writerow({'id':r['content_id'],'판정':verdict,'사유':reason})
