"""Provisional wire contract and adapters live exclusively here."""
import json,re
from pathlib import Path
from .legacy_rules import read_body
TYPES=('창작·각색','사실해설','사실서사','실행안내','해석·감상','원문낭독')
TYPE_ALIASES={re.sub(r'[\s·ㆍ・]','',t):t for t in TYPES}
TYPE_ALIASES['각색']='창작·각색'
CONSUMED={'content_id','title','production_type','production_type_original','script','sources','material'}
def output_metadata(d):
    if not d:return {}
    return {**{k:v for k,v in d.items() if k not in CONSUMED},'production_type':d['production_type'],'production_type_original':d.get('production_type_original',d['production_type'])}
def is_economy(d):return d.get('category') in ('economy','C08')
def checkable_facts(d):return [f for f in d['material'].get('facts',[]) if not isinstance(f,dict) or f.get('kind')!='fiction']
def load(path):
    path=Path(path);d=json.loads(path.read_text())
    for key in ('content_id','title','production_type','script','sources'):
        if key not in d:raise ValueError('missing '+key)
    if not isinstance(d['content_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]+',d['content_id']):raise ValueError('unsafe content_id')
    d['production_type_original']=d['production_type'];d['production_type']=TYPE_ALIASES.get(re.sub(r'[\s·ㆍ・]','',d['production_type']).removesuffix('형'))
    if d['production_type'] not in TYPES:raise ValueError('production_type')
    if not isinstance(d['script'],list) or not d['script'] or not all(isinstance(s,str) and s.strip() for s in d['script']):raise ValueError('script must contain sentences')
    if not isinstance(d['sources'],list):raise ValueError('sources must be a list')
    for key,default in [('category','unknown'),('material',{}),('generator',{})]:
        if d.get(key) is None:d[key]=default
    feedback_revision(d)
    if d.get('target_sec') is not None and (not isinstance(d['target_sec'],(float,int)) or d['target_sec']<=0):raise ValueError('target_sec')
    for i,source in enumerate(d['sources'],1):
        source.setdefault('source_id',f'source-{i}')
        lic=source.get('license') or {};source['license']=lic
        for k in ('kind','commercial','modify','share_alike'):
            if lic.get(k) is None:lic[k]='unknown'
        if isinstance(source.get('text'),str):source['_text']=source['text']
        elif source.get('text_path'):
            p=Path(source['text_path']);p=p if p.is_absolute() else path.parent/p;source['_text']=p.read_text()
    return d

def sentences(body): return [s.strip() for s in re.split(r'\n+|(?<=[.!?])\s+',body) if s.strip()]
def write(d,out,filename=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    (out/((filename or d['content_id'])+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2))
def samples(root,out):
    root=Path(root)
    for kind,typ in [('economy','사실해설'),('life','사실해설'),('story','창작·각색')]:
        folder=root/kind; sources=json.loads((folder/'1_source.json').read_text()); mat=json.loads((folder/'2_extract.json').read_text())
        if mat.get('legal_point'):mat['facts']=[{'id':'f1','text':mat['legal_point']}]
        for s in sources:
            s['license'].setdefault('share_alike',False);s['attribution']=s['license'].get('attribution','');s['source_url']=s.get('url','')
            if s.get('text_path'):s['text_path']=str((root/s['text_path']).resolve())
        for p in folder.glob('3_script*.md'):
            body=read_body(p.read_text()); cid=kind+('_bad' if 'bad' in p.stem else '')
            write(dict(content_id=cid,title=cid,category=kind,production_type=typ,target_sec=60,script=sentences(body),sources=sources,material=mat,generator={'revision':0}),out)
def manifest(repo,out):
    repo=Path(repo)
    for row in json.loads((repo/'content/scripts/_index/production-manifest.json').read_text()):
        fmt=row['format'];typ='원문낭독' if '낭독' in fmt else '창작·각색' if '창작' in fmt else '사실서사' if '사실 기반' in fmt else '사실해설'
        md=(repo/row['script_path']).read_text()
        sources,notes=source_metadata(md)
        write(dict(content_id=row['content_id'],title=row['title'],category=row['topic_code'],format=fmt,production_type=typ,target_sec=row['target_sec'],script=sentences(read_body((repo/row['script_path']).read_text())),sources=sources,material={'details':notes},generator={'revision':0}),out)


def source_metadata(md):
    """Extract declared provenance; do not infer permissions from public access."""
    section3=re.search(r'## 3\.[^\n]*\n(.*?)(?=\n## |\Z)',md,re.S)
    section4=re.search(r'## 4\.[^\n]*\n(.*?)(?=\n## |\Z)',md,re.S)
    evidence='\n'.join(m.group(1) for m in (section3,section4) if m)
    theme_only=('보편적' in evidence and any(x in evidence for x in ('독립 창작','각색·직접 인용이 아닙니다','원사연 각색이 아닙니다')))
    sources=[]
    for i,(title,url) in enumerate(re.findall(r'\[([^\]]+)\]\((https?://[^\s)]+)\)',evidence),1):
        sources.append({'source_id':f'md-source-{i}','source_url':url,'attribution':title,
                        'usage':'theme-only' if theme_only else 'basis',
                        'license':mapped_license(title,url,evidence),
                        'rights_note':evidence})
    return sources,[evidence] if evidence else []


def mapped_license(title,url,evidence):
    kind='unknown'
    if ('ebible.org/eng-web/' in url or 'version=WEB' in url) and 'Public Domain' in evidence:
        kind='PD'
    elif 'law.go.kr' in url and '판결' in evidence and ('제7조' in evidence or '저작권 보호 대상이 아' in evidence):
        kind='copyright-art7'
    elif 'easylaw.go.kr' in url and re.search(r'공공누리\s*(?:제)?1\s*유형',evidence):
        kind='공공누리 1유형'
    return dict(kind=kind,commercial='unknown' if kind=='unknown' else 'yes',modify='unknown' if kind=='unknown' else 'yes',share_alike=False)


def feedback_revision(d):
    value=d.get('generator',{}).get('feedback_revision',0)
    if type(value) is not int or value<0:raise ValueError('generator.feedback_revision must be a nonnegative integer')
    return value
