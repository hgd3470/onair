"""Reassess saved demo content without regeneration or changing source licenses."""
import copy
import re
from pathlib import Path
from filelock import FileLock
from script_agent.io import read_source
from script_agent.models import Handoff, Draft
from script_agent.validation import validate_material, checks
from script_agent.use_assessment import ContentReview, UseAssessment, SourceProcessing, fingerprint, apply_use_assessment
from .core import read, save, digest, now, target_day, Extraction, Verification
from .quality import validate_facts, validate_opinions
from .llm import Models, credentials
from .demo import describe_demo
from script_agent.fact_source import is_independent_fact_source
from .private_review import apply_private_assessment

MUTABLE = {'status', 'rights_status', 'use_assessment', 'release_status'}


class ScriptValidationError(ValueError):
    def __init__(self, problems):
        super().__init__('script_validation_failed')
        self.problems = [p.model_dump() for p in problems]


def record_validation_hold(demo, audit, path, item, out, exc):
    before = copy.deepcopy(out)
    out['status'] = 'hold'
    if out.get('release_status') != 'blocked':
        out['release_status'] = 'pending_content_review'
    Handoff.model_validate(out)
    if protected(out) != protected(before):
        raise ValueError('protected_script_data_changed')
    record = {'id': item['id'], 'stream': item['stream'], 'status': 'hold',
              'facts_review': 'not_run', 'reason': str(exc), 'checks': exc.problems,
              'content_sha256': fingerprint(out), 'preserved_sha256': digest(protected(out)),
              'preserved_content_and_history': True}
    save(audit/'validation-failures'/item['stream']/(item['id']+'.json'), record)
    save(path, out)
    item.update(status='hold', facts_review='not_run', release_status=out['release_status'])
    return record


def protected(value):
    return {k: v for k, v in value.items() if k not in MUTABLE}


def evidence_for(run, demo, item, out):
    """Revalidate provenance rather than trusting a card's cross_checked label."""
    day = target_day(read(run/'manifest.json')['target_date'])
    stream, topic_id = item['stream'], item['id']
    if stream not in {'issue', 'market'} or not re.fullmatch('[a-z][a-z0-9-]{0,60}', topic_id):
        raise ValueError('unsafe_topic_id')
    card = read(demo/'cards'/stream/(topic_id+'.json'))
    collection_cards = read(run/'manifest.json')['streams'][stream]['items']
    if card not in collection_cards:
        raise ValueError('collection_card_mismatch')
    topic = run/stream/'private/topics'/topic_id
    evaluation = read(sorted(topic.glob('evaluation-*.json'))[-1])
    articles = read(topic/'articles.json')
    accepted, rejected = validate_facts(Extraction.model_validate(evaluation['extraction']),
                                       Verification.model_validate(evaluation['verification']), articles, day)
    if not evaluation['verification']['topic_confirmed']:
        raise ValueError('topic_unconfirmed')
    if not card['facts'] or not {f['text'] for f in card['facts']} <= {f['text'] for f in accepted}:
        raise ValueError('fact_card_mismatch')
    opinions, _ = validate_opinions(Extraction.model_validate(evaluation['extraction']),
        Verification.model_validate(evaluation['verification']), articles, accepted)
    if card.get('opinions', []) != opinions:
        raise ValueError('opinion_card_mismatch')
    source = run/stream/card['source_file']
    text, meta, sha = read_source(source)
    copied_source = demo/'sources'/stream/source.name
    if read_source(copied_source) != (text, meta, sha):
        raise ValueError('demo_source_mismatch')
    if sha != out['input_sha256'] or out['sources'][0]['text'] != text or digest(text) != out['sources'][0]['sha256']:
        raise ValueError('source_hash_mismatch')
    if any(f['text'] not in text for f in card['facts']):
        raise ValueError('source_fact_missing')
    from script_agent.news_opinions import parse_opinions, apply_news_attribution
    expected_opinions = [{k: v for k, v in o.items() if k != 'verification'} for o in opinions]
    if parse_opinions(text) != expected_opinions:
        raise ValueError('source_opinion_mismatch')
    if apply_news_attribution(copy.deepcopy(out))['attribution'] != out['attribution']:
        raise ValueError('opinion_attribution_mismatch')
    model = Handoff.model_validate(out)
    validate_material(model.material, text, meta)
    draft = Draft.model_validate({k:out[k] for k in ('title','format','tags','script','claims','adaptation_note','unresolved')} | {'changes':[]})
    problems = checks(draft, model.material, text, meta, out['duration']['chars_per_sec'], 'with_spaces')
    if problems:
        raise ScriptValidationError(problems)
    provenance=None
    if is_independent_fact_source(meta):
        provenance=read(run/stream/'private/provenance'/(topic_id+'.json'))
        if (provenance['source_sha256']!=sha or provenance['source_id']!=meta.source_id
                or provenance['source_body_sha256']!=digest(text)):
            raise ValueError('private_provenance_mismatch')
        urls={s['source_url'] for s in provenance['original_sources']}
        chosen=[f for f in accepted if f['text'] in {f['text'] for f in card['facts']}]
        ids={e['article_id'] for f in chosen for e in f['evidence']}
        if urls!={a['url'] for a in articles if a['id'] in ids}:
            raise ValueError('private_provenance_source_mismatch')
    else:
        urls = {s.source_url for s in meta.original_sources}
    if not urls or not urls <= {a['url'] for a in articles}:
        raise ValueError('missing_original_article')
    # All fetched originals are included, also those not selected in the final facts.
    result={'card':card, 'accepted_facts':accepted, 'evaluation':evaluation,
            'opinions':opinions, 'articles':articles, 'source_sha256':sha, 'rejected_fact_count':len(rejected)}
    if provenance is not None:result['provenance']=provenance
    return result


def opinion_review_context(out):
    """Expose validated adjacent attribution scope without deciding semantic fidelity."""
    from script_agent.models import Material
    from script_agent.news_opinions import opinion_links, names_in_line, speaker_in_line
    draft = Draft.model_validate({k:out[k] for k in ('title','format','tags','script','claims','adaptation_note','unresolved')} | {'changes':[]})
    links = opinion_links(draft, Material.model_validate(out['material']), out['sources'][0]['text'])
    result = []
    for n, opinions in sorted(links.items()):
        for opinion in opinions:
            candidates = [n]
            if {o['id'] for o in links.get(n-1, [])} == {o['id'] for o in opinions}:
                candidates.insert(0, n-1)
            explicit = [i for i in candidates if names_in_line(opinion['attribution'], draft.script[i-1])
                        and speaker_in_line(opinion['speaker'], draft.script[i-1])]
            result.append({'sentence':n, 'opinion_id':opinion['id'], 'attribution':opinion['attribution'],
                           'speaker':opinion['speaker'], 'explicit_attribution_sentences':explicit,
                           'scope':[{'sentence':i,'text':draft.script[i-1]} for i in candidates]})
    return result


def retain_processing_decisions(processing, previous):
    previous = {p['source_url']:SourceProcessing.model_validate(p) for p in previous}
    for entry in processing:
        old = previous.get(entry.source_url)
        if old:
            for op in ('fetch','store','model_transfer'):
                decision = getattr(old, op)
                if old.article_sha256 == entry.article_sha256 or decision.status == 'prohibited':
                    setattr(entry, op, decision.model_copy(deep=True))


def reusable_record(record, out, evidence, private_assessment=None):
    assessment=private_assessment if private_assessment is not None else out.get('use_assessment',{})
    recalculated=(apply_private_assessment(copy.deepcopy(out),assessment,evidence) if private_assessment is not None
                  else apply_use_assessment(copy.deepcopy(out)))
    if (record['content_sha256'] != fingerprint(out)
            or record['preserved_sha256'] != digest(protected(out))
            or record['evidence_sha256'] != digest(evidence)
            or assessment.get('evidence_sha256') != digest(evidence)
            or any(record[k] != out[k] for k in ('status','rights_status','release_status'))
            or recalculated != out):
        raise ValueError('unselected_assessment_stale_include_topic')
    return copy.deepcopy(record)


def assess_demo(root, config, demo_id, topics=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', demo_id):
        raise ValueError('unsafe_demo_id')
    # Dedication is a publisher declaration, not an LLM decision. Legacy outputs
    # receive the same audited metadata adapter before a fresh content review.
    from .publication import publish_demo
    publish_demo(root, demo_id)
    demo = root/'demo'/demo_id
    with FileLock(str(demo/'.lock'), timeout=0):
        manifest = read(demo/'manifest.json')
        rid = manifest['collection_run']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', rid):
            raise ValueError('unsafe_run_id')
        day = target_day(manifest['target_date'])
        run = root/'output'/f'{day:%Y%m%d}'/'runs'/rid
        selected = set(topics) if topics is not None else {x['id'] for x in manifest['scripts']}
        if not selected or not selected <= {x['id'] for x in manifest['scripts']}:
            raise ValueError('unknown_review_topic')
        audit = demo/'use-review'
        audit.mkdir(parents=True, exist_ok=True)
        for name in ('manifest.json','summary.txt','scripts.txt'):
            path=demo/name
            backup=audit/'before'/name
            if path.exists() and not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(path.read_bytes())
        previous_report = read(audit/'verification.json') if (audit/'verification.json').exists() else {'scripts':[]}
        previous_records = {(x['stream'],x['id']):x for x in previous_report['scripts']}
        models = Models(root, config, credentials(root), audit/'model-logs')
        report = {'policy':'news-attribution-v2', 'at':now(), 'scripts':[],
                  'human_approved':False, 'tts_generated':False}
        for item in manifest['scripts']:
            if item['status']=='failed':
                raise ValueError('demo_script_incomplete')
            path = (demo/item['output']).resolve()
            if not path.is_relative_to((demo/'scripts').resolve()):
                raise ValueError('unsafe_output_path')
            out = read(path)
            before = copy.deepcopy(out)
            key = item['stream']+'/'+item['id']
            try:
                evidence = evidence_for(run, demo, item, out)
            except ScriptValidationError as exc:
                if item['id'] not in selected:
                    raise
                report['scripts'].append(record_validation_hold(demo, audit, path, item, out, exc))
                completed = {(x['stream'], x['id']) for x in report['scripts']}
                pending = [x for key, x in previous_records.items() if key not in completed]
                save(audit/'verification.json', report | {'status': 'in_progress',
                                                         'scripts': report['scripts'] + pending})
                save(demo/'manifest.json', manifest)
                print(f'use-review/{key}: hold / script_validation_failed; continuing', flush=True)
                continue
            private=is_independent_fact_source(out['input_metadata'])
            old_record=previous_records.get((item['stream'],item['id']))
            old_assessment=(read(demo/old_record['assessment_report']) if private and old_record and old_record.get('assessment_report')
                            else (out.get('use_assessment') or {}))
            if item['id'] not in selected:
                previous = previous_records.get((item['stream'],item['id']))
                if previous is None:
                    raise ValueError('unselected_assessment_missing_include_topic')
                report['scripts'].append(reusable_record(previous, out, evidence,old_assessment if private else None))
                print(f'use-review/{key}: existing assessment retained', flush=True)
                continue
            payload = {'title':out['title'], 'script':out['script'], 'facts':evidence['card']['facts'],
                       'opinions':evidence['opinions'], 'attribution':out['attribution'],
                       'opinion_attribution_context':opinion_review_context(out),
                       'claims':out['claims'], 'material':out['material'], 'articles':evidence['articles'], 'content_sha256':fingerprint(out),
                       'evidence_sha256':digest(evidence)}
            # Content-addressed assessment includes implementation so changed gates get fresh logs.
            implementation = {str(p.relative_to(root.parent)):digest(p.read_text()) for p in (
                Path(__file__), root/'src/issue_collector/quality.py',
                root/'src/issue_collector/private_review.py',
                root.parent/'script_agent/src/script_agent/use_assessment.py',
                root.parent/'script_agent/src/script_agent/news_opinions.py')}
            payload['implementation'] = implementation
            review = models.call('rights_review', payload, ContentReview)
            record_dir = audit/'records'/key/(digest(payload)[:20])
            if not (record_dir/'before.json').exists():
                save(record_dir/'before.json', before)
            save(record_dir/'evidence.json', evidence)
            processing = [SourceProcessing(source_url=a['url'], article_sha256=digest(a['text']))
                          for a in {a['url']:a for a in evidence['articles']}.values()]
            # Preserve human decisions on unchanged evidence. A previous prohibition
            # survives even changed article text; rerunning a content review cannot waive it.
            retain_processing_decisions(processing, old_assessment.get('processing',[]))
            # Unknown is intentionally retained. This command cannot grant processing permission.
            from script_agent.news_opinions import HEADER as OPINION_HEADER
            opinion_policy = out['sources'][0]['text'].startswith(OPINION_HEADER + '\n')
            assessment = UseAssessment(policy_version='news-attribution-v2' if opinion_policy else 'facts-separation-v1',
                basis='facts_with_optional_attributed_opinions' if opinion_policy else 'independently_rewritten_facts',
                content_sha256=fingerprint(out), evidence_sha256=digest(evidence),
                evidence_report=str((record_dir/'evidence.json').relative_to(demo)),
                reviewed_at=now(), reviewer=config['models']['verifier']['model'], evidence_verified=True,
                content=review, processing=processing)
            if private:
                apply_private_assessment(out,assessment.model_dump(),evidence)
            else:
                out['use_assessment'] = assessment.model_dump()
                apply_use_assessment(out)
            Handoff.model_validate(out)
            if protected(out) != protected(before):
                raise ValueError('protected_script_data_changed')
            save(record_dir/'assessment.json', assessment.model_dump())
            save(path, out)
            for field in ('status','rights_status','release_status'):
                item[field] = out[field]
            item['facts_review'] = review.verdict
            record = {'id':item['id'], 'stream':item['stream'], 'before_status':before['status'],
                      'status':out['status'], 'rights_status':out['rights_status'], 'release_status':out['release_status'],
                      'facts_review':review.verdict, 'rationale':review.rationale, 'findings':review.findings,
                      'preserved_content_and_history':protected(out)==protected(before),
                      'preserved_sha256':digest(protected(out)), 'content_sha256':fingerprint(out),
                      'evidence_sha256':digest(evidence), 'processing_sources':len(processing),
                      'processing_permissions':sorted({getattr(p,op).status for p in processing for op in ('fetch','store','model_transfer')}), 'record':str(record_dir.relative_to(demo))}
            if private:record['assessment_report']=str((record_dir/'assessment.json').relative_to(demo))
            report['scripts'].append(record)
            completed = {(x['stream'],x['id']) for x in report['scripts']}
            pending_records = [x for key,x in previous_records.items() if key not in completed]
            save(audit/'verification.json', report | {'status':'in_progress',
                                                      'scripts':report['scripts'] + pending_records})
            save(demo/'manifest.json', manifest)
            print(f"use-review/{key}: {out['status']} / {out['release_status']}", flush=True)
        manifest['use_review_at'] = now()
        save(demo/'manifest.json', manifest)
        summary = [f"이슈 수집 에이전트 데모 | {day.isoformat()}", describe_demo(manifest['scripts']),
                   '사실 재서술 검토와 수집·저장·모델 전송 권한을 분리함. 사람 승인·TTS 미실행.', '']
        script_text = summary.copy()
        for item in manifest['scripts']:
            out = read(demo/item['output'])
            line = f"{item['stream']} / {item['title']}: {out['status']}, 사실 재서술 {item['facts_review']}, 서비스 {out['release_status']}"
            summary.append(line)
            script_text += [line, *out['script'], '']
        (demo/'summary.txt').write_text('\n'.join(summary)+'\n')
        (demo/'scripts.txt').write_text('\n'.join(script_text)+'\n')
        report['status'] = 'completed'
        report['all_facts_review_pass'] = all(x['facts_review']=='pass' for x in report['scripts'])
        report['all_ready_for_review'] = all(x['status']=='ready_for_downstream_review' for x in report['scripts'])
        save(audit/'verification.json', report)
        return 0 if report['all_ready_for_review'] else 2
