"""Run the unchanged pipeline, then compute labeled error-set metrics."""
import csv,json,re
from collections import defaultdict
from pathlib import Path
from .input import load,output_metadata
from .rules import check,combine,issue,normalize_item
from .llm import Client,review,Refusal,adjudicate_numbers,adjudicate_overlaps
FLAWS={'copy','unique','number','overstated','unsupported','harmful','realname','driving','listen','none'}
BLOCKED={'revise','human_review','reject'}

def metrics(truth,result):
    items=result['items'];stage=truth['expected_stage'];sent=truth['sent']
    stage_hit=bool(stage) and any((x['stage']==stage or x['stage'].startswith(stage+'-')) and (sent is None or (type(x.get('sent')) is int and x['sent']>0 and abs(x['sent']-sent)<=1)) for x in items)
    detected=result['decision'] in BLOCKED
    return dict(case_id=truth['case_id'],base_id=truth['base_id'],flaw=truth['flaw'],decision=result['decision'],expected_decision=truth['expected_decision'],decision_detected=detected,stage_detected=stage_hit,false_positive=truth['flaw']=='none' and detected,fired_stages=';'.join(sorted({x['stage'] for x in items})))

def evaluate(cases,truth_path,out,model='claude-haiku-5-5',kanana_url='http://127.0.0.1:8101',ignore_stages=(),prompt_style="xml-ko",budget_root=None):
    from .__main__ import safety,processing_error,write_batch_reports
    cases=Path(cases);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    truths=[json.loads(line) for line in Path(truth_path).read_text().splitlines() if line.strip()]
    ids=set();inputs=[]
    for t in truths:
        # Some authored sets use explicit no-stage/no-sentence sentinels.
        if t.get('expected_stage') == 'pass':t['expected_stage']=None
        if t.get('sent') == 0:t['sent']=None
        for key in ('case_id','base_id','flaw','sent','expected_stage','expected_decision','note'):
            if key not in t:raise ValueError('truth missing '+key)
        if not re.fullmatch(r'[A-Za-z0-9_-]+',t['case_id']) or t['case_id'] in ids:raise ValueError('invalid/duplicate case_id')
        if not isinstance(t['flaw'],str) or not t['flaw']:raise ValueError('invalid flaw')
        if t['sent'] is not None and (type(t['sent']) is not int or t['sent']<1):raise ValueError('invalid truth sent')
        if t['expected_stage'] is not None and not re.fullmatch(r'R[0-9]+[A-Za-z]?(?:-[A-Za-z0-9]+)*',t['expected_stage']):raise ValueError('invalid expected_stage')
        if t['expected_decision'] not in ('revise','human_review','reject','pass'):raise ValueError('invalid expected_decision')
        ids.add(t['case_id']);inputs.append(cases/(t['case_id']+'.json'))
    rows=[];view_rows=[];all_calls=[];results=[]
    def run_case(pair):
        t,path=pair
        d=None;client=None;items=[];raw={};fun=[];revision=0;error=False
        try:
            d=load(path)
            client=Client(out/'cases'/t['case_id']/'cache',model,prompt_style=prompt_style,budget_root=budget_root or out)
            items=check(d)
            try:
                llm_items,raw,fun=review(d,client)
                if 'R4' in raw:items=[x for x in items if not (x['stage']=='R4' and (x['reason'].startswith('유해 표현: ') or x['reason']=='운전 중 위험 동작 지시'))]
                items=adjudicate_numbers(d,client,items,raw);items=adjudicate_overlaps(d,client,items,raw)
                items+=llm_items
            except Refusal as exc:items.append(issue('LLM','human_review','refusal: '+str(exc)))
            if kanana_url!='skip':si,sr=safety(d,kanana_url);items+=si;raw['R4-a']=sr
            from .input import feedback_revision
            revision=feedback_revision(d)
            decisions=[combine([x for x in items if x['stage'] not in ignore_stages],revision),combine(items,revision),combine([x for x in items if x['stage']!='R1'],revision)]
        except Exception as exc:
            items.append(processing_error(exc));decisions=['human_review']*3;error=True;revision=0
        result=dict(pipeline_version='refine-v1.0.5',prompt_style=prompt_style,case_id=t['case_id'],revision=revision,round=revision+1,expected_seconds=round(sum(len(re.findall('[가-힣]',x)) for x in d['script'])/6.1,2) if d else None,content_id=d['content_id'] if d else t['case_id'],decision=decisions[0],decision_as_is=decisions[1],decision_without_r1=decisions[2],ignored_stages=list(ignore_stages),items=items,raw=raw,fun_items=fun,fun_score=sum(x['score'] for x in fun) if fun else None,calls=client.calls if client else [],processing_error=error)
        result={**output_metadata(d),**result}
        result['items']=[normalize_item(x) for x in result['items']]
        (out/(t['case_id']+'.refine.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(t['case_id'],result['decision'],flush=True)
        views=[dict(view=view,**metrics(t,{**result,'decision':result[key]})) for view,key in [('as_is','decision_as_is'),('without_R1','decision_without_r1')]]
        return metrics(t,result),result['calls'],views,result
    from concurrent.futures import ThreadPoolExecutor
    pairs=list(zip(truths,inputs))
    workers=2 if model=="claude-sonnet-5-5" else 4
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for offset in range(0,len(pairs),workers):
            for row,calls,views,result in pool.map(run_case,pairs[offset:offset+workers]):
                rows.append(row);all_calls.extend(calls);view_rows.extend(views);results.append(result)
    write_batch_reports([{**r,'content_id':r['case_id']} for r in results],out)
    summary=write_report(rows,out)
    write_report(view_rows,out,'_views')
    calls=[x for x in all_calls if not x['cached']]
    cost=dict(input_tokens=sum(x['usage']['input_tokens'] for x in calls),output_tokens=sum(x['usage']['output_tokens'] for x in calls),cost_usd=sum(x['cost_usd'] for x in calls))
    (out/'cost.json').write_text(json.dumps(cost,indent=2))
    print('flaw | n | decision rate | stage rate | original FP rate')
    for x in summary:print(f"{x['flaw']} | {x['n']} | {x['decision_rate']} | {x['stage_rate']} | {x['false_positive_rate']}")
    print(json.dumps(cost));return rows,summary


def write_report(rows,out,suffix=""):
    fields=(['view'] if rows and 'view' in rows[0] else [])+['case_id','base_id','flaw','decision','expected_decision','decision_detected','stage_detected','false_positive','fired_stages']
    with (out/('eval_cases'+suffix+'.csv')).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    groups=defaultdict(list)
    for row in rows:groups[(row.get('view',''),row['flaw'])].append(row)
    summary=[]
    for (view,flaw),group in sorted(groups.items()):
        n=len(group);original=flaw=='none'
        summary.append(dict(**({'view':view} if view else {}),flaw=flaw,n=n,decision_detected='' if original else sum(r['decision_detected'] for r in group),decision_rate='' if original else sum(r['decision_detected'] for r in group)/n,stage_detected='' if original else sum(r['stage_detected'] for r in group),stage_rate='' if original else sum(r['stage_detected'] for r in group)/n,false_positives=sum(r['false_positive'] for r in group) if original else '',false_positive_rate=sum(r['false_positive'] for r in group)/n if original else ''))
    fields=(['view'] if rows and 'view' in rows[0] else [])+['flaw','n','decision_detected','decision_rate','stage_detected','stage_rate','false_positives','false_positive_rate']
    with (out/('eval_summary'+suffix+'.csv')).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(summary)
    return summary
