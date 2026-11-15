"""Canonical source ownership and generated artifact locations."""
import hashlib
import os
import re
import shutil
from pathlib import Path

from .io import read_source


def batch_code(source_id):
    match = re.match(r'^(\d{6}-\d{4})(?:-|$)', source_id or '')
    return match.group(1) if match else '_unbatched'


def source_library(root):
    return root.parent / 'source_collector' / 'output'


def canonical_source(root, source_id, input_digest=None):
    # Issue Markdown inputs are owned by this standalone agent after queue completion.
    folder = (root / 'archive' / (input_digest or '_legacy') if (source_id or '').startswith('IC-')
              else source_library(root) / batch_code(source_id))
    for ext in ('.md', '.txt'):
        path = folder / (source_id + ext)
        if path.exists():
            return path
    return folder / (source_id + '.md')


def output_directory(root, source_id):
    return root / 'output' / batch_code(source_id)


def log_directory(root, destination, content_id):
    if destination.resolve().is_relative_to((root / 'output').resolve()):
        return root / 'logs' / content_id
    return destination / 'logs' / content_id


def source_reference(root, destination, source_id, input_digest=None):
    return os.path.relpath(canonical_source(root, source_id, input_digest), destination)


def complete_job(root, job_dir, source_id):
    """Keep originals at collector, retain only state under agent after success."""
    import json
    state = json.loads((job_dir / 'job.json').read_text())
    source = job_dir / state['filename']
    input_digest = read_source(source)[2] if source_id.startswith('IC-') else None
    target = canonical_source(root, source_id, input_digest)
    if target.exists():
        # Sidecars and frontmatter may encode the same source differently.
        if read_source(target) != read_source(source):
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            target = target.parent / 'variants' / digest / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
        sidecar = source.with_suffix('.meta.json')
        if sidecar.exists():
            shutil.copy2(sidecar, target.with_suffix('.meta.json'))
    source.unlink()
    source.with_suffix('.meta.json').unlink(missing_ok=True)
    done = root / 'state' / 'completed'
    done.mkdir(parents=True, exist_ok=True)
    job_dir.replace(done / job_dir.name)
