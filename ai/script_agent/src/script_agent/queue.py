from .paths import output_directory, complete_job
import json
import shutil
import uuid
from pathlib import Path

from .io import atomic_json, now, queue_lock, read_source
from .providers import ProviderError
from .errors import safe_code


def process_queue(root: Path, pipeline, limit=100, mode=None):
    mode = mode or pipeline.config.get('pipeline', {}).get('mode', 'standard')
    if mode == 'batch':
        from .batch import process_batch_queue
        return process_batch_queue(root, pipeline, limit=limit)
    if limit < 1:
        raise ValueError('limit_must_be_positive')
    for folder in ('input', 'processing', 'failed', 'output'):
        (root / folder).mkdir(exist_ok=True)
    results = []
    with queue_lock(root):
        # A claimed job persists through interruption. Stages with saved results resume.
        jobs = sorted((root / 'processing').glob('*/job.json'))
        for path in sorted((root / 'input').iterdir()):
            if len(jobs) >= limit:
                break
            if path.suffix.lower() not in {'.txt', '.md'} or path.is_symlink():
                continue
            job_dir = root / 'processing' / uuid.uuid4().hex
            job_dir.mkdir()
            state = {'filename': path.name, 'claimed_at': now(), 'status': 'claiming'}
            atomic_json(job_dir / 'job.json', state)
            path.replace(job_dir / path.name)
            sidecar = path.with_suffix('.meta.json')
            if sidecar.exists():
                sidecar.replace(job_dir / sidecar.name)
            state['status'] = 'claimed'
            atomic_json(job_dir / 'job.json', state)
            jobs.append(job_dir / 'job.json')
        for job_path in jobs[:limit]:
            job_dir = job_path.parent
            state = json.loads(job_path.read_text())
            source_file = job_dir / state['filename']
            # Recover interruption during the two-file claim without losing metadata.
            if state['status'] == 'claiming':
                original = root / 'input' / state['filename']
                if not source_file.exists() and original.exists():
                    original.replace(source_file)
                sidecar = original.with_suffix('.meta.json')
                if sidecar.exists() and not source_file.with_suffix('.meta.json').exists():
                    sidecar.replace(source_file.with_suffix('.meta.json'))
            try:
                text, meta, digest = read_source(source_file, pipeline.config['pipeline']['max_source_chars'])
                output_path = output_directory(root, meta.source_id) / ('S2-' + digest[:16] + '.json')
                if output_path.exists():
                    out = json.loads(output_path.read_text())
                    if out['input_sha256'] != digest:
                        raise ValueError('content_id_collision')
                    duplicate = True
                else:
                    out = pipeline.run(text, meta, digest, output_directory(root, meta.source_id))
                    duplicate = False
                state.update(status='completed', completed_at=now(), content_id=out['content_id'],
                             output_status=out['status'], duplicate=duplicate)
                atomic_json(job_path, state)
                complete_job(root, job_dir, meta.source_id or ('src-' + digest[:16]))
                results.append(state)
            except Exception as exc:
                # Explicitly safe diagnostics only; never str/repr of arbitrary exceptions.
                state.update(status='failed', failed_at=now(), error_type=type(exc).__name__)
                if isinstance(exc, ProviderError):
                    state.update(provider=exc.provider, model=exc.model, code=exc.code,
                                 http_status=exc.status, diagnostics=exc.diagnostics)
                else:
                    state['code'] = safe_code(exc)
                atomic_json(job_path, state)
                job_dir.replace(root / 'failed' / job_dir.name)
                results.append(state)
                if isinstance(exc, ProviderError) and exc.status in {401, 403, 429}:
                    break  # Account-wide failures must not consume every input.
    return results


def retry_job(root, job_id):
    if not job_id.isalnum():
        raise ValueError('invalid_job_id')
    with queue_lock(root):
        source = root / 'failed' / job_id
        job = json.loads((source / 'job.json').read_text())
        job['status'] = 'claimed'
        atomic_json(source / 'job.json', job)
        source.replace(root / 'processing' / job_id)
