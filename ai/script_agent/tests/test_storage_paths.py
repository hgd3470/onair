"""Storage ownership, partitioning and source retention without provider calls."""
import json

from script_agent.io import atomic_json, read_source
from script_agent.paths import batch_code, canonical_source, output_directory
from script_agent.queue import process_queue


def test_batch_code_and_output_partition(tmp_path):
    assert batch_code('261109-1228-C06-01') == '261109-1228'
    assert batch_code('fixture-explanation') == '_unbatched'
    assert output_directory(tmp_path, '261109-1228-C06-01') == tmp_path/'output/261109-1228'


def test_successful_input_is_owned_only_by_collector_and_deduplicated(tmp_path):
    root = tmp_path/'script_agent'
    pending = root/'input'
    pending.mkdir(parents=True)
    content = '---\nsource_id: 261109-1228-C06-01\ncategory: C06\n---\n실행할 원문입니다.'
    (pending/'source.md').write_text(content)
    original = read_source(pending/'source.md')

    class Pipe:
        config = {'pipeline': {'max_source_chars': 60000}}
        calls = 0
        def run(self, text, meta, digest, destination):
            self.calls += 1
            out = {'content_id': 'S2-'+digest[:16], 'input_sha256': digest, 'status': 'hold'}
            atomic_json(destination/(out['content_id']+'.json'), out)
            return out

    pipe = Pipe()
    assert process_queue(root, pipe)[0]['status'] == 'completed'
    canonical = canonical_source(root, original[1].source_id)
    assert canonical == tmp_path/'source_collector/output/261109-1228/261109-1228-C06-01.md'
    assert read_source(canonical) == original
    assert not (root/'archive').exists()
    assert not list((root/'state/completed').rglob('*.md'))
    assert len(list((root/'output/261109-1228').glob('S2-*.json'))) == 1
    (pending/'source-again.md').write_text(content)
    assert process_queue(root, pipe)[0]['duplicate']
    assert pipe.calls == 1
    assert len(list(canonical.parent.glob('*.md'))) == 1
    assert len(list((root/'state/completed').glob('*/job.json'))) == 2


def test_issue_sources_with_same_id_keep_distinct_embedded_references(tmp_path):
    from script_agent.paths import source_reference
    root = tmp_path / 'standalone'
    pending = root / 'input'
    pending.mkdir(parents=True)
    source_id = 'IC-20261111-issue-same-topic'

    class Pipe:
        config = {'pipeline': {'max_source_chars': 160000}}
        def run(self, text, meta, digest, destination):
            out = {'content_id': 'S2-'+digest[:16], 'input_sha256': digest, 'status': 'hold',
                   'text_path': source_reference(root, destination, meta.source_id, digest)}
            atomic_json(destination/(out['content_id']+'.json'), out)
            return out

    for body in ('첫 번째 수집 원문입니다.', '새 실행에서 수집한 원문입니다.'):
        (pending / 'input.md').write_text(f'---\nsource_id: {source_id}\n---\n{body}')
        assert process_queue(root, Pipe())[0]['status'] == 'completed'
    outputs = list((root / 'output').rglob('S2-*.json'))
    assert len(outputs) == 2
    originals = []
    for path in outputs:
        out = json.loads(path.read_text())
        source = path.parent / out['text_path']
        assert read_source(source)[2] == out['input_sha256']
        originals.append(source.resolve())
    assert len(set(originals)) == 2
    assert all(p.is_relative_to(root / 'archive') for p in originals)
    assert not (tmp_path / 'source_collector').exists()
