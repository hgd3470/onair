"""Collector-only team copyright declaration. Never relabel or approve source rights."""
import copy
import re
from pathlib import Path

from filelock import FileLock
from script_agent.models import Handoff
from script_agent.news_context import HEADER, LEGACY_HEADER, ORIGINAL_HEADER
from script_agent.news_opinions import HEADER as OPINION_HEADER, apply_news_attribution
from script_agent.fact_source import FACTS_HEADER, is_independent_fact_source
from script_agent.use_assessment import fingerprint
from .core import digest, now, read, save

from .copyright import POLICY, LICENSE, NOTES


def apply_output_license(out):
    """Return a copy, changing only three existing top-level fields.

    The publisher's declaration is not a content review. Holds, restrictions,
    processing decisions, model evidence and attribution inside sources survive.
    """
    Handoff.model_validate(out)
    source = out['sources'][0]
    independent=is_independent_fact_source(out['input_metadata'])
    if ((not independent and out['input_metadata']['source_kind'] != 'user_summary')
            or out['input_metadata'].get('author') != '이슈 수집 에이전트'
            or not source['text'].startswith((HEADER+'\n', LEGACY_HEADER+'\n',FACTS_HEADER+'\n',ORIGINAL_HEADER+'\n',OPINION_HEADER+'\n'))
            or out['production_type'] == '원문낭독형'):
        raise ValueError('collector_generated_script_required')
    result = copy.deepcopy(out)
    result['license'] = LICENSE
    if source['text'].startswith((ORIGINAL_HEADER + '\n', OPINION_HEADER + '\n')):
        apply_news_attribution(result)
    else:
        result['attribution'] = ''
    # Replace the obsolete declaration; immutable transaction snapshots retain history.
    result['rights_notes'] = [n for n in result.get('rights_notes', [])
                              if not n.startswith('[collector-script-cc0-v1]')]
    notes = result['rights_notes']
    notes.extend(note for note in NOTES if note not in notes)
    Handoff.model_validate(result)
    if fingerprint(result) != fingerprint(out):
        raise ValueError('publication_changed_reviewed_content')
    return result


def local_path(demo, relative):
    path = (demo/relative).resolve()
    if not path.is_relative_to(demo.resolve()):
        raise ValueError('unsafe_publication_path')
    return path


def verify_publication_export(demo, record, review_before, out):
    """Verify the exact authorized overlay on an older model-review snapshot."""
    from .use_review import protected
    path = local_path(demo, record['publication_export'])
    transaction = read(path)
    if transaction['policy'] != POLICY:
        raise ValueError('publication_policy_mismatch')
    matches = [w for w in transaction['writes'] if w['before'].get('content_id') == out['content_id']]
    if len(matches) != 1:
        raise ValueError('publication_snapshot_missing')
    before, after = matches[0]['before'], matches[0]['after']
    if (apply_output_license(before) != after
            or protected(before) != protected(review_before)
            or protected(after) != protected(out)
            or record['preserved_sha256'] != digest(protected(out))):
        raise ValueError('publication_snapshot_mismatch')


def publish_demo(root, demo_id):
    """Offline metadata migration with immutable snapshots and restartable writes."""
    from .use_review import protected
    if not re.fullmatch(r'[A-Za-z0-9_-]+', demo_id):
        raise ValueError('unsafe_demo_id')
    demo = root/'demo'/demo_id
    with FileLock(str(demo/'.lock'), timeout=0):
        pending = demo/'publication/pending.json'
        if pending.exists():
            transaction_path = local_path(demo, read(pending)['transaction'])
            transaction = read(transaction_path)
        else:
            manifest = read(demo/'manifest.json')
            report_path = demo/'use-review/verification.json'
            report = read(report_path) if report_path.exists() else None
            updated_report = copy.deepcopy(report)
            writes = []
            for item in manifest['scripts']:
                if item['status'] == 'failed':
                    continue
                path = local_path(demo, item['output'])
                if not path.is_relative_to((demo/'scripts').resolve()):
                    raise ValueError('unsafe_output_path')
                before = read(path)
                after = apply_output_license(before)
                if before == after:
                    continue
                if report:
                    records = [r for r in updated_report['scripts'] if (r['stream'],r['id']) == (item['stream'],item['id'])]
                    if len(records) != 1 or records[0]['preserved_sha256'] != digest(protected(before)):
                        raise ValueError('publication_review_stale')
                    records[0]['preserved_sha256'] = digest(protected(after))
                writes.append({'path':item['output'], 'before':before, 'after':after})
            if not writes:
                print('Output license already applied; no changes.', flush=True)
                return 0
            relative = 'publication/'+POLICY+'-'+digest(writes)[:20]+'.json'
            if report:
                changed_ids = {w['after']['content_id'] for w in writes}
                for item in manifest['scripts']:
                    if item['status'] != 'failed' and read(local_path(demo,item['output']))['content_id'] in changed_ids:
                        row = next(r for r in updated_report['scripts'] if (r['stream'],r['id']) == (item['stream'],item['id']))
                        row['publication_export'] = relative
                writes.append({'path':'use-review/verification.json','before':report,'after':updated_report})
            transaction = {'policy':POLICY, 'at':now(), 'writes':writes,
                           'content_regenerated':False, 'model_calls':0, 'schema_changed':False}
            transaction_path = demo/relative
            save(transaction_path, transaction)
            save(pending, {'transaction':relative})
        if transaction.get('policy') != POLICY:
            raise ValueError('publication_policy_changed_use_new_transaction')
        # Validate every path/state before any overwrite. Resume accepts only the
        # original snapshot or the exact intended result, never unrelated edits.
        for entry in transaction['writes']:
            if read(local_path(demo,entry['path'])) not in (entry['before'],entry['after']):
                raise ValueError('publication_destination_changed')
        for entry in transaction['writes']:
            save(local_path(demo,entry['path']), entry['after'])
        save(transaction_path.with_suffix('.completed.json'), {'at':now(),'status':'completed'})
        pending.unlink()
        print(f'Applied {LICENSE}; content, sources and review states preserved.', flush=True)
        return 0
