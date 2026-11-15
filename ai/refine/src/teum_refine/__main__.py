from concurrent.futures import ThreadPoolExecutor
from copy import copy
import argparse,csv,json,hashlib,urllib.request
from pathlib import Path
from .input import load,output_metadata,feedback_revision
from .rules import check,combine,issue,kanana_items,listening_checks,normalize_item,merge_r2
from .llm import Client,review,Refusal,adjudicate_numbers,adjudicate_overlaps

def safety_messages(d):
 request='운전하면서 들을 짧은 이야기를 들려줘' if d['production_type'] in ('창작·각색','사실서사') else '운전하면서 들을 짧은 정보를 알려줘'
 return [{'role':'user','content':request},{'role':'assistant','content':'\n'.join(d['script'])}]

def safety(d,url):
 def req(path,data=None):
  request=urllib.request.Request(url+path,data=json.dumps(data).encode() if data else None,headers={'Content-Type':'application/json'})
  with urllib.request.urlopen(request,timeout=120) as r:return json.load(r)
 model=req('/v1/models')['data'][0]['id']
 # vLLM /tokenize uses the server model's actual chat template, matching smoke_safeguard.py.
 messages=safety_messages(d)
 tokens=req('/tokenize',{'model':model,'messages':messages,'add_generation_prompt':False})['tokens']
 result=req('/v1/completions',{'model':model,'prompt':tokens,'max_tokens':1,'temperature':0,'skip_special_tokens':False})['choices'][0]['text']
 return kanana_items(result),result

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True);r=sub.add_parser('run');r.add_argument('input',nargs='?');r.add_argument('--site-kit');r.add_argument('--export-compare',action='store_true');r.add_argument('--out',required=True);r.add_argument('--model',choices=['claude-sonnet-5-5','claude-haiku-5-5'],default='claude-haiku-5-5');r.add_argument('--compare-only',action='store_true');r.add_argument('--skip-dedup',action='store_true');r.add_argument('--dedup-index');r.add_argument('--skip-llm',action='store_true');r.add_argument('--skip-safety',action='store_true');r.add_argument('--syllables-per-sec',type=float,default=6.1);r.add_argument('--kanana-url',default='http://127.0.0.1:8101')
 e=sub.add_parser('eval');e.add_argument('cases');e.add_argument('truth');e.add_argument('--out',required=True);e.add_argument('--model',choices=['claude-sonnet-5-5','claude-haiku-5-5'],default='claude-haiku-5-5');e.add_argument('--kanana-url',default='http://127.0.0.1:8101');e.add_argument('--ignore-stages',nargs='+',default=[]);r.add_argument('--ignore-stages',nargs='+',default=[]);r.add_argument('--prompt-style',choices=['v0','xml-ko'],default='xml-ko');e.add_argument('--prompt-style',choices=['v0','xml-ko'],default='xml-ko');r.add_argument('--workers',type=int);r.add_argument('--no-prompt-cache',action='store_true');r.add_argument('--api-mode',choices=['online','batch'],default='online');r.add_argument('--batch-poll',type=float,default=30);args=p.parse_args()
 if args.command=='run' and args.workers is not None and args.workers<1:p.error('--workers must be positive')
 if args.command=='eval':
  from .evaluation import evaluate
  evaluate(args.cases,args.truth,args.out,args.model,args.kanana_url,args.ignore_stages,args.prompt_style)
  return
 if args.site_kit:
  from .site import convert_kit
  args.input=str(convert_kit(args.site_kit,Path(args.out)/'inputs'))
 if not args.input:p.error('input or --site-kit is required')
 out=Path(args.out);out.mkdir(parents=True,exist_ok=True);c=None;root=Path(args.input);paths=sorted(root.glob('*.json')) if root.is_dir() else [root];results=[];seen=set();used_outputs=set();archive=None
 if not args.skip_dedup and not args.compare_only:
  from .dedup import Archive
  archive=Archive(args.dedup_index or out/'dedup-index.jsonl')
 # Each worker owns its audit list; transport and persistent cache are shared.
 if not args.skip_llm:c=Client(out/'cache',args.model,prompt_style=args.prompt_style,budget_root=out,prompt_cache=not args.no_prompt_cache,api_mode=args.api_mode,batch_poll=args.batch_poll)
 def prepare(path):
  local=copy(c) if c else None
  if local:local.calls=[]
  d=None;items=[];raw={};fun=[];rule_hits=[];error=None
  try:
   d=load(path)
   rule_hits=listening_checks(d['script']);items=rule_hits.copy() if args.compare_only else check(d,args.syllables_per_sec)
   if local:
    try:
     li,raw,fun=review(d,local,args.compare_only)
     if 'R4' in raw:items=[x for x in items if not (x['stage']=='R4' and (x['reason'].startswith('유해 표현: ') or x['reason']=='운전 중 위험 동작 지시'))]
     items=adjudicate_numbers(d,local,items,raw);items=adjudicate_overlaps(d,local,items,raw)
     items+=li
    except Refusal as e:items.append(issue('LLM','human_review','refusal: '+str(e)))
   if not args.skip_safety and not args.compare_only:
    si,sr=safety(d,args.kanana_url);items+=si;raw['R4-a']=sr
  except Exception as exc:error=exc
  return d,items,raw,fun,rule_hits,local.calls if local else [],error
 with ThreadPoolExecutor(max_workers=args.workers or (min(256,max(1,len(paths))) if args.api_mode=="batch" else 8)) as pool:
  prepared=pool.map(prepare,paths)
  for path,state in zip(paths,prepared):
   d,items,raw,fun,rule_hits,script_calls,error=state;dedup_row=None
   try:
    if d:
     if d['content_id'] in seen:
      items=[];raw={};fun=[]
      raise DuplicateId('중복 관리번호')
     seen.add(d['content_id'])
    if error:raise error
    if archive:
     di,dedup_row=archive.consider(d);items+=di
    items=merge_r2(items)
    decision=combine([x for x in items if x['stage'] not in args.ignore_stages],feedback_revision(d));result=dict(content_id=d['content_id'],decision=decision,items=items,r5a_rule_hits=len(rule_hits),fun_score=sum(x['score'] for x in fun) if fun else None,fun_items=fun,raw=raw,skipped=[s for s,skip in [('LLM',args.skip_llm),('R4-a',args.skip_safety),('R6',args.skip_dedup or args.compare_only)] if skip],input_hash=hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest(),calls=script_calls,round=feedback_revision(d)+1,syllables_per_sec=args.syllables_per_sec,expected_seconds=round(sum(len(__import__('re').findall('[가-힣]',x)) for x in d['script'])/args.syllables_per_sec,2))
   except Exception as exc:
    error=processing_error(exc)
    if isinstance(exc,DuplicateId):error.update(problem='중복 관리번호',reason='중복 관리번호',fix='배치 안에서 고유한 관리번호로 바꾸기')
    result=dict(content_id=d['content_id'] if d else path.stem,decision='human_review',items=items+[error],r5a_rule_hits=0,fun_score=None,fun_items=fun,raw=raw,calls=script_calls,round=feedback_revision(d)+1 if d else 1,processing_error=True)
   result={**output_metadata(d),**result}
   result['pipeline_version']='refine-v1.0.5'
   result['items']=[normalize_item(x) for x in result['items']]
   if archive and dedup_row:archive.accept(dedup_row,result['decision'])
   output_id=result['content_id'];suffix=2
   while output_id in used_outputs:output_id=result['content_id']+'__'+str(suffix);suffix+=1
   used_outputs.add(output_id);result['output_id']=output_id
   (out/(output_id+'.refine.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2));results.append(result);print(result['content_id'],result['decision'],result['fun_score'],flush=True)
 if archive:archive.commit()
 write_batch_reports(results,out)
 if args.site_kit:
  from .site import export_results
  export_results(results,out)
 if args.compare_only or args.export_compare:
  export_compare(results,out)
 calls=[x for result in results for x in result['calls'] if not x['cached']]
 cost=dict(cache_read_input_tokens=sum(x['usage'].get('cache_read_input_tokens',0) for x in calls),cache_creation_input_tokens=sum(x['usage'].get('cache_creation_input_tokens',0) for x in calls),input_tokens=sum(x['usage']['input_tokens'] for x in calls),output_tokens=sum(x['usage']['output_tokens'] for x in calls),cost_usd=sum(x['cost_usd'] for x in calls));(out/'cost.json').write_text(json.dumps(cost));print(json.dumps(cost))
class DuplicateId(ValueError):pass

def processing_error(exc):
 return {**issue('R0','human_review',type(exc).__name__+': '+str(exc)), 'problem':'처리 오류'}

def write_batch_reports(results,out):
 with (out/'summary.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=['content_id','decision','fun_score']);w.writeheader();w.writerows({k:r[k] for k in w.fieldnames} for r in results)
 with (out/'revise.jsonl').open('w') as f:
  for r in results:
   normalized=[normalize_item(x) for x in r['items']]
   work=[{k:v for k,v in x.items() if k!='decision'} for x in normalized if x['fixable'] and x['decision']=='revise']
   warnings=[{k:v for k,v in x.items() if k!='decision'} for x in normalized if x['decision']=='pass_with_flags']
   blocking=[{k:v for k,v in x.items() if k!='decision'} for x in normalized if not x['fixable']]
   if r['decision'] in ('revise','human_review') and work:
    f.write(json.dumps(dict(script_id=r['content_id'],output_id=r.get('output_id',r['content_id']),round=r['round'],decision=r['decision'],items=work,blocking=blocking,warnings=warnings),ensure_ascii=False)+'\n')

def export_compare(results,out):
 fields=['content_id','r5a_pass','listening','structure','style','tts','q1','q2','q3','q4','q5','fun_score','overall_fun','r5a_rule_hits','hard_major']
 with (out/'compare.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
  for r in results:
   if r.get('processing_error') or 'overall_fun' not in r['raw'] or 'R5a' not in r['raw'] or 'items' not in r['raw']['R5a']:
    w.writerow(dict(content_id=r['content_id'],r5a_pass='human_review'));continue
   items=r['raw']['R5a']['items'];scores=[x['score'] for x in items]
   rule_count=r.get('r5a_rule_hits',0);hard_major=sum(x.get('severity','major')=='major' for x in r['raw']['R5a'].get('named',{}).get('hard_sents',[]))
   row=dict(r5a_rule_hits=rule_count,hard_major=hard_major,content_id=r['content_id'],r5a_pass='pass' if sum(scores)/4>=3.5 and min(scores)>2 and not rule_count and not hard_major else 'revise',fun_score=r['fun_score'],overall_fun=r['raw']['overall_fun'])
   # Prompts enumerate these four axes in this order; verify names before export.
   for key,token in [('listening','귀'),('structure','구성'),('style','문체'),('tts','TTS')]:
    matches=[x for x in items if token in x['item']]
    if len(matches)!=1:raise ValueError('ambiguous quality item '+key)
    row[key]=matches[0]['score']
   row.update({x['id'].lower(): 'U' if x['answer']=='uncertain' else 'Y' if x['answer'] else 'N' for x in r['fun_items']})
   w.writerow(row)
if __name__=='__main__':main()

