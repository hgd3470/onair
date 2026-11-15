import hashlib
import json
from pathlib import Path

from .io import atomic_json
from .models import Handoff


def export_bundle(output: Path, destination: Path, include_hold=False):
    if destination.exists():
        raise ValueError('destination_must_be_new')
    destination.mkdir(parents=True)
    included = []
    for path in sorted(output.rglob('S2-*.json')):
        data = json.loads(path.read_text())
        Handoff.model_validate(data)
        if data['status'] == 'hold' and not include_hold:
            continue
        for source in data['sources']:
            # Source evidence stays inline; exports do not duplicate source files.
            raw = source['text'].encode()
            if hashlib.sha256(raw).hexdigest() != source['sha256']:
                raise ValueError('source_digest_mismatch')
        atomic_json(destination / path.name, data)
        included.append({'content_id': data['content_id'], 'status': data['status'], 'fixture': data['fixture']})
    atomic_json(destination / 'manifest.json', {'schema_version': '1.0.0', 'items': included,
                                              'approval': 'pending; not a server upload'})
    return {'count': len(included), 'destination': str(destination)}
