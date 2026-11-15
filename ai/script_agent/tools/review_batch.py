"""One source per category; immutable input, resumable independent pipeline outputs."""
import argparse
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from script_agent.io import atomic_json, load_config, now, queue_lock, read_source
from script_agent.pipeline import Pipeline, validate_editorial_brief
from script_agent.providers import ProviderError, credentials
from script_agent.errors import safe_code

ROOT = Path(__file__).resolve().parents[1]

def prepare(root, dest, categories, editorial_briefs=None):
    allowed = {f'C{i:02}' for i in range(1, 22)}
    if not categories or len(set(categories)) != len(categories) or not set(categories) <= allowed:
        raise ValueError('Categories must be unique C01 through C21 codes')
    editorial_briefs = {} if editorial_briefs is None else editorial_briefs
    if not isinstance(editorial_briefs, dict) or not set(editorial_briefs) <= set(categories):
        raise ValueError('invalid_editorial_brief')
    editorial_briefs = {cat: validate_editorial_brief(value) for cat, value in editorial_briefs.items()}
    config = load_config(root)
    plan_file = dest / 'selection.json'
    if plan_file.exists():
        plan = json.loads(plan_file.read_text())
        if (plan['categories'] != categories or plan['config'] != config
                or plan.get('editorial_briefs', {}) != editorial_briefs):
            raise ValueError('Selection/config changed: use a new run directory')
        return plan
    grouped = {c: [] for c in categories}
    for path in sorted((root / 'input').iterdir()):
        if path.suffix.lower() not in {'.md', '.txt'} or path.is_symlink():
            continue
        text, meta, digest = read_source(path, config['pipeline']['max_source_chars'])
        if meta.category in grouped:
            grouped[meta.category].append((path, text, meta, digest))
    cases = []
    for category in categories:
        if not grouped[category]:
            raise ValueError(f'Missing input: {category}')
        path, text, meta, digest = grouped[category][0]
        cases.append({'category': category, 'source_id': meta.source_id or ('SRC-' + digest[:16]),
                      'input_path': str(path.relative_to(root)), 'input_sha256': digest,
                      'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
                      'source_text': text, 'input_metadata': meta.model_dump(),
                      'source_title': text.splitlines()[0].lstrip('# ').strip(),
                      'candidate_count': len(grouped[category])})
    plan = {'schema_version': '1.0.0', 'batch_id': dest.name, 'created_at': now(),
            'categories': categories, 'selection_rule': 'category metadata; first filename lexicographically',
            'config': config, 'cases': cases}
    if editorial_briefs:
        plan['editorial_briefs'] = editorial_briefs
    atomic_json(plan_file, plan)
    return plan

def run(root, dest, plan, workers):
    keys = credentials(root=root)
    stop = threading.Event()
    def case_run(case):
        cat = case['category']
        case_dir = dest / 'cases' / cat
        final = case_dir / ('S2-' + case['input_sha256'][:16] + '.json')
        if final.exists():
            print(cat, 'cached', flush=True)
            return
        if stop.is_set():
            return
        text, meta, digest = read_source(root / case['input_path'], plan['config']['pipeline']['max_source_chars'])
        if digest != case['input_sha256']:
            raise ValueError(f'Input changed: {cat}')
        atomic_json(case_dir / 'run-status.json', {'status': 'running', 'at': now()})
        print(cat, 'running', flush=True)
        try:
            out = Pipeline(root, plan['config'], keys).run(
                text, meta, digest, case_dir,
                editorial_brief=plan.get('editorial_briefs', {}).get(cat))
            state = {'status': 'completed', 'content_id': out['content_id'],
                     'handoff_status': out['status'], 'at': now()}
        except Exception as exc:
            state = {'status': 'failed', 'code': safe_code(exc), 'at': now()}
            if isinstance(exc, ProviderError):
                state.update(code=exc.code, provider=exc.provider, http_status=exc.status, diagnostics=exc.diagnostics)
                if exc.status in {401, 403, 429} or exc.code == 'missing_key':
                    stop.set()
        atomic_json(case_dir / 'run-status.json', state)
        print(cat, json.dumps(state, ensure_ascii=False), flush=True)
    with queue_lock(dest), ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(case_run, plan['cases']))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--categories', nargs='+', default=[f'C{i:02}' for i in range(6,22)])
    parser.add_argument('--workers', type=int, default=3, choices=range(1,5))
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--editorial-briefs', type=Path, help='JSON mapping category to whole-script requirements; not sentence revisions')
    args = parser.parse_args()
    dest = args.destination.resolve()
    briefs = json.loads(args.editorial_briefs.read_text(encoding='utf-8')) if args.editorial_briefs else None
    plan = prepare(ROOT, dest, args.categories, briefs)
    print(json.dumps([{'category': c['category'], 'source_id': c['source_id'], 'chars': len(c['source_text'])} for c in plan['cases']], ensure_ascii=False), flush=True)
    if not args.prepare_only:
        run(ROOT, dest, plan, args.workers)

if __name__ == '__main__':
    main()
