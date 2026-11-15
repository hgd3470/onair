import argparse
import json
import sys
import time
from pathlib import Path

from .io import atomic_json, load_config, queue_lock, read_source
from .pipeline import Pipeline, parse_feedback
from .providers import ProviderError, credentials
from .queue import process_queue, retry_job
from .errors import safe_code


def main():
    parser = argparse.ArgumentParser(description='S2-05 Korean audio script prototype')
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--env-file', type=Path, help='Runtime-only dotenv path (default: <root>/.env); never printed')
    parser.add_argument('--prompt-version', help='Explicit immutable prompt directory version')
    parser.add_argument('--max-source-chars', type=int,
                        help='Explicit input limit, 1..256000 characters (config default unchanged)')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run', help='Claim txt/md, generate, self-review, complete')
    run.add_argument('--limit', type=int, default=100)
    run.add_argument('--mode', '-m', choices=['standard', 'batch'],
                     help='Execution mode: standard (realtime) or batch (Gemini Batch API, 50%% discount)')
    generate = sub.add_parser('generate', help='Generate JSON directly from one txt/md; preserve the input')
    generate.add_argument('path', type=Path)
    generate.add_argument('--destination', type=Path, help='Output directory; default: configured output partition')
    generate.add_argument('--mode', '-m', choices=['standard', 'batch'])
    watch = sub.add_parser('watch')
    watch.add_argument('--interval', type=int, default=15)
    watch.add_argument('--mode', '-m', choices=['standard', 'batch'],
                       help='Execution mode: standard or batch')
    sub.add_parser('preflight')
    retry = sub.add_parser('retry')
    retry.add_argument('job_id')
    validate = sub.add_parser('validate-input')
    validate.add_argument('path', type=Path)
    feedback = sub.add_parser('revise')
    feedback.add_argument('output', type=Path)
    feedback.add_argument('feedback', type=Path)
    feedback.add_argument('--expected-version', help='Reject stale sentence-number feedback')
    feedback.add_argument('--mode', '-m', choices=['standard', 'batch'])
    export = sub.add_parser('export')
    export.add_argument('destination', type=Path)
    export.add_argument('--include-hold', action='store_true')
    experiment = sub.add_parser('experiment')
    experiment.add_argument('--cases', type=Path, default=Path('examples/sources'))
    experiment.add_argument('--rounds', type=int, default=1)
    args = parser.parse_args()
    root = args.root.resolve()
    config = load_config(root)
    if getattr(args, 'mode', None):
        config['pipeline']['mode'] = args.mode
    if args.max_source_chars is not None:
        if not 1 <= args.max_source_chars <= 256000:
            parser.error('max-source-chars must be between 1 and 256000')
        config['pipeline']['max_source_chars'] = args.max_source_chars
    if args.prompt_version:
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]+', args.prompt_version):
            parser.error('invalid prompt version')
        config['pipeline']['prompt_version'] = args.prompt_version
    try:
        if args.command == 'validate-input':
            text, meta, digest = read_source(args.path, config['pipeline']['max_source_chars'])
            from .rights import resolve_rights
            resolved, notes, status = resolve_rights(meta)
            print(json.dumps({'valid': True, 'characters': len(text), 'production_type': meta.production_type,
                              'sha256': digest, 'license': resolved.license.model_dump(),
                              'rights_status': status, 'rights_notes': notes}, ensure_ascii=False))
            return 0
        if args.command == 'retry':
            retry_job(root, args.job_id)
            print('Job queued in processing; run explicitly to retry.')
            return 0
        if args.command == 'export':
            from .handoff import export_bundle
            print(json.dumps(export_bundle(root / 'output', args.destination, args.include_hold), ensure_ascii=False))
            return 0
        if args.command == 'preflight':
            from .preflight import run
            return 0 if run(root, args.env_file) else 2
        pipeline = Pipeline(root, config, credentials(args.env_file, root=root))
        if args.command == 'generate':
            from .paths import output_directory
            text, meta, digest = read_source(args.path, config['pipeline']['max_source_chars'])
            destination = (args.destination.resolve() if args.destination else output_directory(root, meta.source_id))
            if destination == root / 'output':
                destination = output_directory(root, meta.source_id)
            with queue_lock(root):
                if (destination / ('S2-' + digest[:16] + '.json')).exists():
                    raise ValueError('output_exists_use_new_destination')
                out = pipeline.run(text, meta, digest, destination, source_file=args.path)
            print(json.dumps({'output': str(destination/(out['content_id']+'.json')),
                              'status': out['status'], 'content_id': out['content_id']}, ensure_ascii=False))
            return 0
        if args.command == 'experiment':
            from .experiments import run_experiments
            print(json.dumps(run_experiments(root, pipeline, args.cases, args.rounds), ensure_ascii=False))
            return 0
        if args.command == 'revise':
            with queue_lock(root):
                target = json.loads(args.output.read_text(encoding='utf-8'))
                if args.feedback.suffix.lower() == '.jsonl' and not args.expected_version:
                    raise ValueError('feedback_expected_version_required')
                normalizations = []
                issues = parse_feedback(args.feedback, target['content_id'], normalization_notes=normalizations)
                out = pipeline.apply_feedback(args.output, issues, args.expected_version,
                                              feedback_normalizations=normalizations)
            print(json.dumps({'content_id': out['content_id'], 'status': out['status'], 'revision': out['generator']['revision'],
                              'feedback_revision': out['generator']['feedback_revision']}))
            return 0
        mode = getattr(args, 'mode', None)
        if not mode:
            if sys.stdin.isatty() and args.command == 'run':
                configured = config.get('pipeline', {}).get('mode', 'standard')
                print("[Script Agent] 실행 모드를 선택하세요:", file=sys.stderr)
                print("  1) standard : 실시간 순차 처리 (기존 모드)", file=sys.stderr)
                print("  2) batch    : Gemini Batch API 비동기 일괄 처리 (50% 비용 절감, 고병렬 처리)", file=sys.stderr)
                def_choice = '2' if configured == 'batch' else '1'
                try:
                    choice = input(f"모드 선택 [1/2, 기본값: {def_choice}]: ").strip()
                    if choice == '2':
                        mode = 'batch'
                    elif choice == '1':
                        mode = 'standard'
                    else:
                        mode = configured
                except (EOFError, KeyboardInterrupt):
                    mode = configured
            else:
                mode = config.get('pipeline', {}).get('mode', 'standard')
        print(f"[Script Agent] Starting with mode: {mode}", file=sys.stderr)
        config['pipeline']['mode'] = mode
        while True:
            results = process_queue(root, pipeline, getattr(args, 'limit', 100), mode=mode)
            print(json.dumps(results, ensure_ascii=False), flush=True)
            if args.command == 'run':
                return 2 if any(r['status'] == 'failed' for r in results) else 0
            if any(r.get('http_status') in {401, 403, 429} for r in results):
                return 2
            time.sleep(max(1, args.interval))
    except KeyboardInterrupt:
        print('Interrupted; claimed jobs and completed stages are preserved.', file=sys.stderr)
        return 130
    except Exception as exc:
        error = {'error_type': type(exc).__name__}
        if isinstance(exc, ProviderError):
            error.update(code=exc.code, status=exc.status, provider=exc.provider)
            if exc.diagnostics:
                error['diagnostics'] = exc.diagnostics
        else:
            error['code'] = safe_code(exc)
        print(json.dumps(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
