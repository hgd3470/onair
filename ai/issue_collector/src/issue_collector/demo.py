"""Non-destructive script_agent integration, with self-contained demo provenance."""
import concurrent.futures
import copy
import os
import re
import shutil
from pathlib import Path
from filelock import FileLock
from script_agent.io import load_config, read_source
from script_agent.models import Handoff
from script_agent.pipeline import Pipeline
from script_agent.providers import ProviderError
from .core import digest, now, read, save, target_day
from .llm import credentials
from .publication import apply_output_license


from .editorial import news_brief


def demo_selection(collected, streams, count):
    if not 1 <= count <= 20:
        raise ValueError('count_out_of_range')
    streams=list(dict.fromkeys(streams))
    if not streams or not set(streams)<={'issue','market'}:
        raise ValueError('invalid_demo_streams')
    if not set(streams)<=set(collected['streams']):
        raise ValueError('requested_streams_missing')
    if any(len(collected['streams'][s]['items'])<count for s in streams):
        raise ValueError('insufficient_collected_items')
    return [(stream,card) for stream in streams for card in collected['streams'][stream]['items'][:count]]


def describe_demo(scripts):
    parts=[]
    for stream,label in [('issue','이슈'),('market','국내 시장')]:
        n=sum(x['stream']==stream for x in scripts)
        if n: parts.append(f'{label} {n}편')
    parts.append(f"대본 {sum(x['status']!='failed' for x in scripts)}편")
    return ' + '.join(parts)


def build_demo(root,config,args):
    day=target_day(args.date)
    for value in [args.run_id,args.demo_id or args.run_id]:
        if not re.fullmatch(r'[A-Za-z0-9_-]+',value): raise ValueError('unsafe_run_id')
    run=root/'output'/f'{day:%Y%m%d}'/'runs'/args.run_id
    collected=read(run/'manifest.json')
    if collected['status']!='complete': raise ValueError('collection_not_complete')
    streams=list(dict.fromkeys(getattr(args,'streams',['issue','market'])))
    count=getattr(args,'count',3)
    selection=demo_selection(collected,streams,count)
    demo=root/'demo'/(args.demo_id or args.run_id)
    agent=root.parent/'script_agent'
    sc=load_config(agent)
    # Preserve model identity and use the explicitly supplied collector credentials.
    sc=copy.deepcopy(sc)
    sc['models']['generator'].update(config['models']['generator'])
    sc['models']['generator']['max_output_tokens']=32000
    sc['models']['reviewer'].update(config['models']['verifier'])
    sc['pipeline']['mode']=config['collection'].get('mode','standard')
    contract={'collection_sha256':digest(collected),'script_config':sc,
              'streams':streams,'items_per_stream':count,
              'collector_integration_sha256':digest(Path(__file__).read_text()),
              'editorial_sha256':digest((root/'src/issue_collector/editorial.py').read_text()),
              'publication_sha256':digest((root/'src/issue_collector/publication.py').read_text()),
              'script_code':{str(p.relative_to(agent)):digest(p.read_text()) for p in sorted((agent/'src').rglob('*.py'))},
              'prompts':{str(p.relative_to(agent)):digest(p.read_text()) for p in sorted((agent/'prompts'/sc['pipeline']['prompt_version']).glob('*')) if p.is_file()},
              'feedback_scope_sha256':digest((agent/'prompts/feedback-scope.txt').read_text())}
    demo.mkdir(parents=True,exist_ok=True)
    with FileLock(str(demo/'.lock'),timeout=0):
        if (demo/'contract.json').exists():
            if not args.resume: raise ValueError('demo_exists_use_resume_or_new_demo_id')
            if read(demo/'contract.json')!=contract: raise ValueError('demo_contract_changed_use_new_demo_id')
        else:
            save(demo/'contract.json',contract)
        keys=credentials(root)
        jobs=[]
        for stream,card in selection:
            source=run/stream/card['source_file']
            target=demo/'sources'/stream/source.name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
            if source.with_suffix('.meta.json').exists():  # legacy two-file inputs
                shutil.copy2(source.with_suffix('.meta.json'),target.with_suffix('.meta.json'))
            save(demo/'cards'/stream/(card['id']+'.json'),card)
            jobs.append((stream,card,target))
        def one(job):
            stream,card,source=job
            destination=demo/'scripts'/stream/card['id']
            text,meta,sha=read_source(source)
            output=destination/('S2-'+sha[:16]+'.json')
            try:
                if output.exists():
                    value=read(output); Handoff.model_validate(value)
                    if value['input_sha256']!=sha: raise ValueError('demo_source_changed')
                else:
                    brief=news_brief(day,stream,config)
                    value=Pipeline(agent,sc,keys).run(text,meta,sha,destination,editorial_brief=brief)
                    # Existing script_agent assumes source_collector ownership. Adapt only this demo's pointer.
                    old=value['sources'][0]['text_path']
                    value['sources'][0]['text_path']=os.path.relpath(source,destination)
                    Handoff.model_validate(value)
                    save(output,value)
                    save(destination/'integration.json',{'adapter':'issue_collector','original_text_path':old,
                        'text_path':value['sources'][0]['text_path'],'source_file_sha256':digest(source.read_text()),
                        'source_body_sha256':digest(text),'note':'Only the source reference was adapted; script, counters and reviews unchanged.'})
                value=apply_output_license(value)
                save(output,value)
                (destination/'script.txt').write_text(value['title']+'\n\n'+'\n'.join(value['script'])+'\n')
                print(f'demo/{stream}/{card["id"]}: {value["status"]}, review={value["self_review"][-1]["verdict"]}',flush=True)
                return {'stream':stream,'id':card['id'],'title':value['title'],'output':str(output.relative_to(demo)),
                        'status':value['status'],'review':value['self_review'][-1]['verdict'],'rights_status':value['rights_status'],
                        'revision':value['generator']['revision'],'sentences':len(value['script']),
                        'estimated_sec':value['duration']['estimated_sec'],'tts_checked':False}
            except Exception as exc:
                code=exc.code if isinstance(exc,ProviderError) else type(exc).__name__
                save(destination/'demo-error.json',{'code':code,'at':now()})
                print(f'demo/{stream}/{card["id"]}: error {code}',flush=True)
                return {'stream':stream,'id':card['id'],'status':'failed','code':code}
        with concurrent.futures.ThreadPoolExecutor(config['script']['workers']) as pool:
            results=list(pool.map(one,jobs))
        manifest={'title':'이슈 수집 에이전트 데모','target_date':day.isoformat(),'collection_run':args.run_id,
                  'status':'complete' if all(x['status']!='failed' for x in results) else 'partial',
                  'source_count':len(jobs),'script_count':sum(x['status']!='failed' for x in results),'scripts':results,
                  'human_approved':False,'tts_generated':False,'at':now()}
        save(demo/'manifest.json',manifest)
        summary=[f'이슈 수집 에이전트 데모 | {day.isoformat()}', describe_demo(results),
                 '권리 상태 및 사람 승인은 별도. 실제 음성 합성은 하지 않음.','']
        for x in results: summary.append(f"{x['stream']} / {x.get('title',x['id'])}: {x['status']}, 자체검토 {x.get('review','미완료')}")
        (demo/'summary.txt').write_text('\n'.join(summary)+'\n')
        save(root/'demo/latest.json',{'path':str(demo.relative_to(root/'demo')),'status':manifest['status']})
        print(str(demo),flush=True)
    if manifest['status']!='complete':
        return 2
    from .use_review import assess_demo
    return assess_demo(root,config,args.demo_id or args.run_id)
