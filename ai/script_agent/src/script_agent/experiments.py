"""Resumable capped experiments; API failures are attempts, not quality rounds."""
import json
from datetime import datetime
from pathlib import Path

from .io import atomic_json, now, queue_lock, read_source
from .providers import ProviderError


def run_experiments(root, pipeline, cases, rounds):
    settings = pipeline.config['experiments']
    deadline = datetime.fromisoformat(settings['deadline'])
    pipeline.deadline = deadline
    cap = min(30, settings['max_rounds'])
    if rounds < 1 or rounds > cap:
        raise ValueError('rounds_outside_cap')
    experiment_root = root / 'experiments'
    ledger_path = experiment_root / 'ledger.json'
    with queue_lock(experiment_root):
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {'rounds': [], 'attempts': []}
        if 'models' in ledger and ledger['models'] != pipeline.config['models']:
            raise ValueError('experiment_models_must_remain_fixed')
        ledger['models'] = pipeline.config['models']
        ledger['unit'] = 'one_source_end_to_end_execution; batches may contain several units'
        ledger['max_executions'] = cap
        ledger.pop('stopped', None)
        def execution_count():
            return len([p for p in (experiment_root / 'runs').glob('*/logs/S2-*') if p.is_dir()])
        for _ in range(rounds):
            if execution_count() >= cap or datetime.now(deadline.tzinfo) >= deadline:
                ledger['stopped'] = 'cap_or_deadline'
                break
            round_number = len(ledger['rounds']) + 1
            version = pipeline.config['pipeline']['prompt_version']
            destination = experiment_root / 'runs' / f'{round_number:02d}-{version}'
            record = {'round': round_number, 'prompt_version': version, 'started_at': now(), 'cases': []}
            try:
                paths = sorted(p for p in cases.iterdir() if p.suffix in {'.txt', '.md'})
                if not paths:
                    raise ValueError('no_experiment_sources')
                for path in paths:
                    if datetime.now(deadline.tzinfo) >= deadline:
                        raise ValueError('deadline_reached')
                    text, meta, digest = read_source(path)
                    if execution_count() >= cap and not (destination / 'logs' / ('S2-' + digest[:16])).exists():
                        ledger['stopped'] = 'execution_cap'
                        break
                    outpath = destination / ('S2-' + digest[:16] + '.json')
                    out = json.loads(outpath.read_text()) if outpath.exists() else pipeline.run(text, meta, digest, destination)
                    record['cases'].append({'content_id': out['content_id'], 'type': out['production_type'],
                        'status': out['status'], 'revision': out['generator']['revision'],
                        'scores': out['self_review'][-1]['scores'], 'remaining': out['unresolved'],
                        'estimated_sec': out['duration']['estimated_sec']})
                    print(json.dumps({'batch': round_number, 'prompt': version,
                                      'type': out['production_type'], 'status': out['status'],
                                      'revision': out['generator']['revision']}, ensure_ascii=False), flush=True)
                record['finished_at'] = now()
                ledger['rounds'].append(record)
                ledger['execution_count'] = execution_count()
                if ledger['execution_count'] >= cap:
                    ledger['stopped'] = 'execution_cap'
            except Exception as exc:
                record['error_type'] = type(exc).__name__
                if isinstance(exc, ProviderError):
                    record.update(provider=exc.provider, model=exc.model, code=exc.code, http_status=exc.status, diagnostics=exc.diagnostics)
                ledger['attempts'].append(record)
                ledger['stopped'] = 'blocked_or_incomplete_round'
                atomic_json(ledger_path, ledger)
                return ledger
            atomic_json(ledger_path, ledger)
        atomic_json(ledger_path, ledger)
        return ledger
