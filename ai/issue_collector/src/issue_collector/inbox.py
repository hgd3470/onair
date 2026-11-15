"""Bounded daily collection and recoverable, non-overwriting inbox delivery.

The inbox receipt survives the consumer moving a Markdown file away. A pending
publication with no destination is ambiguous: never silently enqueue it again.
"""
import hashlib
import os
import re
import tempfile
from pathlib import Path

from filelock import FileLock

from .core import read, save
from . import pipeline


def quotas(limit):
    if type(limit) is not int or not 1 <= limit <= 40:
        raise ValueError('limit_out_of_range')
    return {'issue': limit // 2, 'market': (limit + 1) // 2}


def inbox_path(root, explicit=None):
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    if os.environ.get('TEUM_INBOX'):
        return Path(os.environ['TEUM_INBOX']).expanduser().resolve()
    if os.environ.get('TEUM_HOME'):
        return (Path(os.environ['TEUM_HOME']).expanduser() / 'script_agent/input').resolve()
    return (root.parent / 'script_agent/input').resolve()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _save_receipts(path, receipts):
    save(path, receipts)
    _sync_dir(path.parent)


def _payloads(result, folder, counts, day):
    """Read only accepted manifest entries, never a wildcard of leftover exports."""
    payloads = []
    names = set()
    for stream, quota in counts.items():
        items = result['streams'].get(stream, {}).get('items', [])
        if len(items) != quota:
            raise ValueError('collection_count_mismatch')
        source_root = (folder / stream / 'sources').resolve()
        for item in items:
            path = folder / stream / item['source_file']
            if (path.is_symlink() or path.resolve().parent != source_root or
                path.suffix != '.md' or
                not path.name.startswith(f'IC-{day:%Y%m%d}-{stream}-') or
                item.get('status') != 'ready_for_script' or path.name in names):
                raise ValueError('invalid_inbox_source')
            data = path.read_bytes()
            if not data:
                raise ValueError('empty_inbox_source')
            names.add(path.name)
            payloads.append({'name': path.name, 'stream': stream, 'data': data, 'sha256': _sha(data)})
    return payloads


def deliver(inbox, payloads, *, run_id, day):
    inbox.mkdir(parents=True, exist_ok=True)
    state = inbox / '.issue-collector'
    state.mkdir(exist_ok=True)
    receipt_path = state / 'receipts.json'
    with FileLock(str(state / 'delivery.lock'), timeout=0):
        receipts = read(receipt_path) if receipt_path.exists() else {'version': 1, 'files': {}}
        if receipts.get('version') != 1 or not isinstance(receipts.get('files'), dict):
            raise ValueError('invalid_delivery_receipts')
        files = receipts['files']
        # Detect conflicts across the whole set before exposing any new file.
        existing = set()
        for item in payloads:
            dest = inbox / item['name']
            prior = files.get(item['name'])
            if prior and (prior.get('sha256') != item['sha256'] or
                          prior.get('state') not in {'pending', 'delivered'}):
                raise ValueError('inbox_receipt_conflict')
            if dest.is_symlink():
                raise ValueError('inbox_file_conflict')
            try:
                data = dest.read_bytes()
            except FileNotFoundError:
                pass
            except IsADirectoryError:
                raise ValueError('inbox_file_conflict') from None
            else:
                if _sha(data) != item['sha256']:
                    raise ValueError('inbox_file_conflict')
                existing.add(item['name'])
            if prior and prior['state'] == 'pending' and item['name'] not in existing:
                raise ValueError('delivery_unknown_requires_check')

        delivered = already = 0
        records = []
        for item in payloads:
            dest = inbox / item['name']
            prior = files.get(item['name'])
            if prior and prior['state'] == 'delivered':
                # The consumer may already have moved/deleted the inbox file.
                already += 1
                records.append({'path': str(dest), 'stream': item['stream'], 'status': 'already_delivered'})
                continue
            record = {'sha256': item['sha256'], 'run_id': run_id,
                      'target_date': day.isoformat(), 'state': 'pending'}
            if item['name'] in existing:
                # Identical existing file, or recovery after link + before receipt.
                # The consumer may move it after the preflight read above.
                record['state'] = 'delivered'
                files[item['name']] = record
                _save_receipts(receipt_path, receipts)
                already += 1
                records.append({'path': str(dest), 'stream': item['stream'], 'status': 'already_delivered'})
                continue
            # Staging is on the inbox filesystem. link() exposes the entire file
            # atomically and refuses to overwrite a racing producer's file.
            fd, temporary = tempfile.mkstemp(prefix='handoff-', suffix='.part', dir=state)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(item['data'])
                    handle.flush()
                    os.fsync(handle.fileno())
                files[item['name']] = record
                _save_receipts(receipt_path, receipts)
                os.link(temporary, dest)
                _sync_dir(inbox)
                record['state'] = 'delivered'
                _save_receipts(receipt_path, receipts)
                delivered += 1
                records.append({'path': str(dest), 'stream': item['stream'], 'status': 'delivered'})
            finally:
                Path(temporary).unlink(missing_ok=True)
        return {'delivered': delivered, 'already_delivered': already, 'files': records,
                'receipt_path': str(receipt_path)}


def run_to_inbox(root, config, day, limit, inbox, run_id='inbox-daily-v1', *, dry_run=False):
    counts = quotas(limit)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ValueError('unsafe_run_id')
    root, inbox = Path(root).resolve(), Path(inbox).resolve()
    folder = root / 'output' / f'{day:%Y%m%d}' / 'runs' / run_id
    report = {'status': 'planned', 'target_date': day.isoformat(), 'run_id': run_id,
              'limit': limit, 'requested_counts': counts, 'inbox': str(inbox), 'path': str(folder),
              'execution_mode': config['collection'].get('mode', 'standard')}
    if dry_run:
        return report, 0
    # Resume only the identical run contract; the collector itself rejects
    # changed settings and does not resubmit ambiguous/failed model calls.
    active = {stream: n for stream, n in counts.items() if n}
    result, folder = pipeline.run(root, config, day, list(active), limit, run_id,
                                  resume=True, stream_counts=active)
    collected = {s: len(result['streams'].get(s, {}).get('items', [])) for s in counts}
    report.update(collected_counts=collected, collected=sum(collected.values()),
                  delivered=0, already_delivered=0)
    if any(collected[s] > counts[s] for s in counts):
        raise ValueError('collection_limit_exceeded')
    if result['status'] != 'complete' or collected != counts:
        report['status'] = 'pending' if result['status'] == 'pending' else 'partial'
        report['code'] = ('batch_pending_resume_same_run' if result['status'] == 'pending'
                          else 'collection_incomplete_no_delivery')
        return report, 2
    payloads = _payloads(result, folder, counts, day)
    report.update(deliver(inbox, payloads, run_id=run_id, day=day))
    report['status'] = 'complete'
    return report, 0
