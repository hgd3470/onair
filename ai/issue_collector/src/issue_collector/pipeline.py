import concurrent.futures
from pathlib import Path
from urllib.parse import urlsplit
from filelock import FileLock
from .acquisition import Fetcher, acquire, candidate, canonical, discover
from .catalog import publisher
from .naver import search_news, topic_query
from .copyright import LICENSE, COPYRIGHT, NOTES
from .core import Extraction, Selection, Verification, digest, now, read, save
from .llm import CallFailure, Models, credentials
from .quality import validate_facts, validate_opinions
from .editorial import audience_policy
from . import handoff
from .handoff import export_source
from .batch_transport import BatchPending, BatchError


def search_candidates(models, fetcher, topic, day):
    result=models.search(topic.search_query,day)
    out=[]
    for url in result['urls'][:30]:
        try:
            if urlsplit(url).hostname=='vertexaisearch.cloud.google.com':
                url,_=fetcher._get(url,discovery=True)
            if publisher(url): out.append(candidate(url,method='gemini_search'))
        except Exception:
            continue
    return out


def diverse_origins(items):
    buckets={}
    for item in items:
        buckets.setdefault(item['origin'], []).append(item)
    result=[]
    while any(buckets.values()):
        for bucket in buckets.values():
            if bucket: result.append(bucket.pop(0))
    return result


def collect_stream(root, config, keys, day, stream, folder, count):
    folder.mkdir(parents=True,exist_ok=True)
    if (folder/'manifest.json').exists(): return read(folder/'manifest.json')
    fetcher=Fetcher(folder/'private/cache',config['collection']['http_timeout_sec'])
    models=Models(root,config,keys,folder/'private/model-logs')
    candidate_file=folder/'private/candidates.json'
    if candidate_file.exists():
        candidates=read(candidate_file)
    else:
        candidates=discover(fetcher,stream,day,config['collection'],keys)
        save(candidate_file,candidates)
        save(folder/'acquisition.json',fetcher.events)
        fetcher.events.clear()
    print(f'{stream}: {len(candidates)} candidates',flush=True)
    if not candidates:
        result={'stream':stream,'status':'insufficient','items':[],'reason':'no_candidates','target_date':day.isoformat()}
        save(folder/'manifest.json',result)
        return result
    selected=models.call('select',{'target_date':day.isoformat(),'stream':stream,
                                  'candidate_topics':config['collection']['candidate_topics'],'audience_policy':audience_policy(config),'candidates':candidates},Selection)
    save(folder/'selection.json',selected.model_dump())
    indexed={x['id']:x for x in candidates}
    completed=[]; attempts=[]; used_articles=set(); topic_ids=set()
    for topic in selected.topics[:config['collection']['candidate_topics']]:
        if len(completed)>=count: break
        if topic.id in topic_ids:
            attempts.append({'topic':topic.id,'status':'duplicate_topic_id'}); continue
        topic_ids.add(topic.id)
        topic_dir=folder/'private/topics'/topic.id
        topic_dir.mkdir(parents=True,exist_ok=True)
        try:
            if len(set(topic.candidate_ids)&used_articles)>len(set(topic.candidate_ids))/2:
                attempts.append({'topic':topic.id,'status':'duplicate_event'}); continue
            choices=diverse_origins([indexed[x] for x in dict.fromkeys(topic.candidate_ids) if x in indexed])
            # Acquire candidate articles first; search can fill source gaps without trusting summaries.
            def fetch_all(items):
                with concurrent.futures.ThreadPoolExecutor(config['collection']['http_workers']) as pool:
                    return [x for x in pool.map(lambda c:acquire(fetcher,c,day,config['collection']),items) if x]
            articles=fetch_all(choices[:config['collection']['max_articles_per_topic']])
            if len({x['origin'] for x in articles})<3:
                extra=search_news(fetcher,[topic_query(topic.search_query,day)],day,config['collection'],keys,topic=True)
                existing={canonical(c['url']) for c in choices[:config['collection']['max_articles_per_topic']]}
                extra=[c for c in extra if canonical(c['url']) not in existing]
                present={a['origin'] for a in articles}
                extra=diverse_origins(extra)
                extra.sort(key=lambda c:c['origin'] in present)
                articles+=fetch_all(extra[:config['collection']['max_articles_per_topic']])
            if len({x['origin'] for x in articles})<3 and config['collection']['google_search']:
                try:
                    extra=search_candidates(models,fetcher,topic,day)
                    existing={a['url'] for a in articles}
                    extra=[c for c in extra if canonical(c['url']) not in existing]
                    articles+=fetch_all(extra[:config['collection']['max_articles_per_topic']])
                except CallFailure as exc:
                    save(topic_dir/'search-error.json',{'code':str(exc)})
            articles=diverse_origins(list({a['url']:a for a in articles}.values()))[:config['collection']['max_articles_per_topic']]
            save(topic_dir/'articles.json',articles)
            print(f'{stream}/{topic.id}: {len(articles)} fetched articles',flush=True)
            if len({a['origin'] for a in articles})<config['collection']['min_independent_sources']:
                attempts.append({'topic':topic.id,'status':'insufficient_articles','articles':len(articles)}); continue
            extraction_payload={'target_date':day.isoformat(),'stream':stream,'topic':topic.model_dump(),'articles':articles}
            facts=[]; rejected=[]
            for revision in range(2):
                extraction=models.call('extract',extraction_payload,Extraction)
                verification=models.call('verify',{'target_date':day.isoformat(),'stream':stream,'topic':topic.model_dump(),'extraction':extraction.model_dump(),'articles':articles},Verification)
                facts,rejected=validate_facts(extraction,verification,articles,day,config['collection']['min_independent_sources'])
                opinions, rejected_opinions = validate_opinions(extraction, verification, articles, facts)
                save(topic_dir/f'evaluation-{revision}.json',{'extraction':extraction.model_dump(),'verification':verification.model_dump(),'accepted':facts,'rejected':rejected, 'accepted_opinions':opinions, 'rejected_opinions':rejected_opinions})
                print(f'{stream}/{topic.id}: round {revision}: {len(facts)} verified, {len(rejected)} rejected',flush=True)
                if (len(facts)>=config['collection']['min_facts'] and verification.topic_confirmed
                    and any(f['kind']!='background' and f['id'] in verification.central_fact_ids for f in facts)):
                    break
                extraction_payload={**extraction_payload,'previous_extraction':extraction.model_dump(),'verification_feedback':{'accepted':facts,'rejected':rejected}}
            if (len(facts)<config['collection']['min_facts'] or not verification.topic_confirmed
                or not any(f['kind']!='background' and f['id'] in verification.central_fact_ids for f in facts)):
                attempts.append({'topic':topic.id,'status':'insufficient_facts','accepted':len(facts),'rejected':len(rejected)}); continue
            source=export_source(folder/'sources',stream,topic,facts,articles,day,config['script']['target_sec'], opinions=opinions)
            # Cards remain concise; the separate Markdown handoff includes full original bodies.
            public_facts=[{k:v for k,v in f.items() if k not in {'evidence','verification'}} | {
                'source_urls':[next(a['url'] for a in articles if a['id']==e['article_id']) for e in f['evidence']],
                'verification_status':'cross_checked'} for f in facts]
            card={'id':topic.id,'title':topic.title,'target_date':day.isoformat(),'stream':stream,
                  'facts':public_facts,'opinions':opinions,
                  'license':LICENSE, 'copyright':COPYRIGHT, 'rights_notes':list(NOTES),
                  'attribution':', '.join(dict.fromkeys(o['attribution'] for o in opinions)), 'source_file':str(source.relative_to(folder)),
                  'status':'ready_for_script','publication_approval':'pending','rights_status':'unverified',
                  'selection_reason':topic.reason,'importance':topic.importance,'cautions':verification.cautions}
            save(folder/'cards'/(topic.id+'.json'),card)
            completed.append(card)
            used_articles.update(topic.candidate_ids)
            attempts.append({'topic':topic.id,'status':'accepted','accepted':len(facts),'rejected':len(rejected)})
        except BatchPending:
            # Keep this stream unfinished: replay validated steps and poll the
            # same remote ID on resume instead of freezing a partial manifest.
            raise
        except Exception as exc:
            code=str(exc) if isinstance(exc,(CallFailure,BatchError)) else type(exc).__name__
            save(topic_dir/'error.json',{'code':code,'at':now()})
            attempts.append({'topic':topic.id,'status':'error','code':code})
            print(f'{stream}/{topic.id}: error {code}',flush=True)
        finally:
            # Old discovery results survive a resumed run; new events are append-only by run.
            old=read(folder/'acquisition.json') if (folder/'acquisition.json').exists() else []
            save(folder/'acquisition.json',old+fetcher.events)
            fetcher.events.clear()
    result={'stream':stream,'target_date':day.isoformat(),'status':'complete' if len(completed)>=count else 'partial',
            'requested_count':count,'items':completed,'attempts':attempts,'coverage_notes':selected.coverage_notes,
            'completed_at':now(),'all_events_collected':False}
    save(folder/'manifest.json',result)
    summary=[f'이슈 수집 에이전트 | {day.isoformat()} | {stream}',f'검증 완료 {len(completed)}/{count}편. 전체 뉴스의 망라가 아닌 중요 사안 선별.', COPYRIGHT, '적용 범위: 자체 작성 표현·편집. 객관적 사실 및 제3자 기사·인용 권리 제외.', '']
    for i,card in enumerate(completed,1):
        summary.append(f'{i}. {card["title"]}')
        summary.extend('- '+f['text'] for f in card['facts'])
        summary.extend('- 의견 인용: '+o['attribution']+' — '+o['text'] for o in card['opinions'])
        summary.append('출처: '+' | '.join(sorted({u for f in card['facts'] for u in f['source_urls']})))
        summary.append('')
    summary.append('자동 교차 검증 결과이며 사람 승인·상업 이용 권리 확인·음성 검수 전입니다.')
    (folder/'summary.txt').write_text('\n'.join(summary)+'\n')
    return result


def run(root,config,day,streams,count,run_id,resume=False,*,stream_counts=None):
    # Legacy collect keeps its per-stream count. The inbox runner supplies exact
    # quotas before collection, rather than collecting extra and discarding it.
    if stream_counts is not None:
        if (set(stream_counts)!=set(streams) or
            any(type(n) is not int or n<1 for n in stream_counts.values()) or
            sum(stream_counts.values())!=count):
            raise ValueError('invalid_stream_counts')
    base=root/'output'/f'{day:%Y%m%d}'
    folder=base/'runs'/run_id
    base.mkdir(parents=True,exist_ok=True)
    contract={'target_date':day.isoformat(),'streams':streams,'count':count,'config':config,
              'handoff_sha256':digest(Path(handoff.__file__).read_text()),
              'implementation':{str(p.relative_to(root)):digest(p.read_text()) for pattern in ('src/**/*.py','prompts/*.txt') for p in sorted(root.glob(pattern))}}
    if stream_counts is not None:
        contract['stream_counts']=stream_counts
    with FileLock(str(base/'.lock'),timeout=0):
        if folder.exists():
            if not resume: raise ValueError('run_exists_use_resume_or_new_run_id')
            if read(folder/'contract.json')!=contract: raise ValueError('contract_changed_use_new_run_id')
        else:
            save(folder/'contract.json',contract)
        keys=credentials(root)
        if config['collection'].get('mode')=='batch':
            if config['models']['generator']['provider']!='gemini':
                raise ValueError('batch_requires_gemini_provider')
            if not keys.get('gemini_api_key'):
                raise ValueError('batch_requires_gemini_api_key')
        with concurrent.futures.ThreadPoolExecutor(min(config['collection']['stream_workers'],len(streams))) as pool:
            futures={s:pool.submit(collect_stream,root,config,keys,day,s,folder/s,
                                  stream_counts[s] if stream_counts is not None else count) for s in streams}
            outcomes={}
            for s,f in futures.items():
                try: outcomes[s]=f.result()
                except BatchPending as exc:
                    outcomes[s]={'status':'pending','code':exc.code,'items':[]}
                except Exception as exc:
                    outcomes[s]={'status':'failed','code':str(exc) if isinstance(exc,(CallFailure,BatchError)) else type(exc).__name__,'items':[]}
        status=('pending' if any(x['status']=='pending' for x in outcomes.values()) else
                'complete' if all(x['status']=='complete' for x in outcomes.values()) else 'partial')
        result={'title':'이슈 수집 에이전트','target_date':day.isoformat(),'timezone':'Asia/Seoul','run_id':run_id,
                'status':status,'streams':outcomes,'completed_at':now()}
        save(folder/'manifest.json',result)
        save(base/'latest-attempt.json',{'run_id':run_id,'status':status,'path':str(folder.relative_to(base))})
        if status=='complete':
            # Publish pointer only after all requested streams have completed; don't overwrite a good run with a failed one.
            save(base/'latest.json',{'run_id':run_id,'path':str(folder.relative_to(base))})
            (base/'summary.txt').write_text('\n\n'.join((folder/s/'summary.txt').read_text() for s in streams))
        return result,folder
