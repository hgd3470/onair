import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from issue_collector import inbox, pipeline
from issue_collector.core import Selection, Topic, read, save
from test_quality import DAY, sample


ROOT = Path(__file__).resolve().parents[1]
CONFIG = {'collection': {'stream_workers': 2}}


def export_sample(folder, stream, number):
    ext, _, articles = sample()
    for i, article in enumerate(articles):
        article.update(url=f'https://www.hani.co.kr/arti/{number}-{i}.html',
                       title='공식 발표', publisher=f'편집국 {i}')
    topic = Topic(id=f'topic-{number}', title='공식 발표', reason='공공 영향',
                  importance=4, candidate_ids=['a', 'b'], search_query='발표')
    path = pipeline.export_source(folder / 'sources', stream, topic,
        [f.model_dump() for f in ext.facts], articles, DAY, 60)
    return {'source_file': str(path.relative_to(folder)), 'status': 'ready_for_script'}


@pytest.fixture
def collected(monkeypatch):
    calls = []

    def stream(root, config, keys, day, name, folder, count):
        manifest = folder / 'manifest.json'
        if manifest.exists():
            return read(manifest)
        calls.append((name, count))
        items = [export_sample(folder, name, i) for i in range(count)]
        result = {'status': 'complete', 'items': items}
        save(manifest, result)
        (folder / 'summary.txt').write_text('completed')
        return result

    monkeypatch.setattr(pipeline, 'credentials', lambda root: {})
    monkeypatch.setattr(pipeline, 'collect_stream', stream)
    return calls


@pytest.mark.parametrize('limit,expected', [(1, (0, 1)), (2, (1, 1)), (5, (2, 3)), (6, (3, 3)), (40, (20, 20))])
def test_total_limit_is_applied_before_collection(tmp_path, collected, limit, expected):
    root, dest = tmp_path / 'collector', tmp_path / 'input'
    report, code = inbox.run_to_inbox(root, CONFIG, DAY, limit, dest)
    assert code == 0
    assert report['requested_counts'] == dict(zip(('issue', 'market'), expected))
    assert dict(collected) == {s: n for s, n in report['requested_counts'].items() if n}
    assert report['collected'] == report['delivered'] == limit
    assert len(list(dest.glob('*.md'))) == limit
    # Real handoff exporter -> atomic transfer -> real generator reader.
    from script_agent.io import read_source
    for path in dest.glob('*.md'):
        text, meta, _ = read_source(path)
        assert meta.category == ('C16' if '-issue-' in path.name else 'C08')
        assert '## 수집 원문' in text
        stream = 'issue' if '-issue-' in path.name else 'market'
        original = Path(report['path']) / stream / 'sources' / path.name
        assert path.read_bytes() == original.read_bytes()


def test_repeated_run_does_not_recollect_or_requeue_consumed_files(tmp_path, collected):
    root, dest = tmp_path / 'collector', tmp_path / 'input'
    first, _ = inbox.run_to_inbox(root, CONFIG, DAY, 5, dest)
    for path in dest.glob('*.md'):
        path.unlink()  # generator has claimed/consumed the files
    second, code = inbox.run_to_inbox(root, CONFIG, DAY, 5, dest)
    assert code == 0 and second['delivered'] == 0 and second['already_delivered'] == 5
    assert len(collected) == 2 and not list(dest.glob('*.md'))
    assert first['collected_counts'] == second['collected_counts']


def test_changed_limit_cannot_reuse_daily_run(tmp_path, collected):
    root, dest = tmp_path / 'collector', tmp_path / 'input'
    inbox.run_to_inbox(root, CONFIG, DAY, 5, dest)
    with pytest.raises(ValueError, match='contract_changed'):
        inbox.run_to_inbox(root, CONFIG, DAY, 6, dest)
    assert len(collected) == 2 and len(list(dest.glob('*.md'))) == 5


def test_partial_collection_is_not_delivered_or_backfilled(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, 'credentials', lambda root: {})

    def partial(root, config, keys, day, stream, folder, count):
        calls.append((stream, count))
        n = count - 1 if stream == 'issue' else count
        return {'status': 'partial' if n < count else 'complete',
                'items': [export_sample(folder, stream, i) for i in range(n)]}

    monkeypatch.setattr(pipeline, 'collect_stream', partial)
    report, code = inbox.run_to_inbox(tmp_path / 'collector', CONFIG, DAY, 5, tmp_path / 'input')
    assert code == 2 and report['collected_counts'] == {'issue': 1, 'market': 3}
    assert report['delivered'] == 0 and not (tmp_path / 'input').exists()
    assert dict(calls) == {'issue': 2, 'market': 3}


@pytest.mark.parametrize('limit', [0, -1, 41])
def test_invalid_limit_fails_before_collection(tmp_path, collected, limit):
    with pytest.raises(ValueError, match='limit_out_of_range'):
        inbox.run_to_inbox(tmp_path, CONFIG, DAY, limit, tmp_path / 'input')
    assert not collected and not list(tmp_path.iterdir())


def payload(name='IC-20261111-issue-topic.md', data=b'complete markdown'):
    return {'name': name, 'data': data, 'sha256': inbox._sha(data), 'stream': 'issue'}


def test_conflict_is_found_before_any_delivery(tmp_path):
    items = [payload(), payload('IC-20261111-market-topic.md')]
    (tmp_path / items[1]['name']).write_bytes(b'existing different content')
    with pytest.raises(ValueError, match='inbox_file_conflict'):
        inbox.deliver(tmp_path, items, run_id='test', day=DAY)
    assert not (tmp_path / items[0]['name']).exists()
    assert (tmp_path / items[1]['name']).read_bytes() == b'existing different content'


def test_changed_source_cannot_replace_a_consumed_delivery(tmp_path):
    item = payload()
    inbox.deliver(tmp_path, [item], run_id='first', day=DAY)
    (tmp_path / item['name']).unlink()
    with pytest.raises(ValueError, match='inbox_receipt_conflict'):
        inbox.deliver(tmp_path, [payload(data=b'changed source')], run_id='second', day=DAY)
    assert not (tmp_path / item['name']).exists()


def test_delivery_lock_prevents_concurrent_publication(tmp_path):
    from filelock import FileLock, Timeout
    state = tmp_path / '.issue-collector'
    state.mkdir()
    with FileLock(str(state / 'delivery.lock')):
        with pytest.raises(Timeout):
            inbox.deliver(tmp_path, [payload()], run_id='test', day=DAY)
    assert not list(tmp_path.glob('*.md'))


def test_unexpected_excess_collection_is_not_delivered(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, 'run', lambda *a, **kw: (
        {'status': 'complete', 'streams': {'issue': {'items': [{}, {}, {}]},
                                          'market': {'items': [{}, {}, {}]}}}, tmp_path))
    with pytest.raises(ValueError, match='collection_limit_exceeded'):
        inbox.run_to_inbox(tmp_path, CONFIG, DAY, 5, tmp_path / 'input')
    assert not (tmp_path / 'input').exists()


def test_atomic_file_visible_only_after_complete_staging(tmp_path, monkeypatch):
    item = payload(data=b'body' * 10000)
    link = os.link

    def checked_link(src, dest):
        assert not Path(dest).exists()
        assert Path(src).read_bytes() == item['data']
        assert Path(src).suffix == '.part'
        link(src, dest)

    monkeypatch.setattr(inbox.os, 'link', checked_link)
    assert inbox.deliver(tmp_path, [item], run_id='test', day=DAY)['delivered'] == 1
    assert (tmp_path / item['name']).read_bytes() == item['data']


@pytest.mark.parametrize('consumer_claims', [False, True])
def test_interrupt_after_publish_never_requeues(tmp_path, monkeypatch, consumer_claims):
    item = payload()
    real_save = inbox._save_receipts

    def interrupted_save(path, receipts):
        if receipts['files'][item['name']]['state'] == 'delivered':
            if consumer_claims:
                (tmp_path / item['name']).unlink()
            raise OSError('simulated crash before final receipt')
        real_save(path, receipts)

    monkeypatch.setattr(inbox, '_save_receipts', interrupted_save)
    with pytest.raises(OSError):
        inbox.deliver(tmp_path, [item], run_id='test', day=DAY)
    monkeypatch.setattr(inbox, '_save_receipts', real_save)
    if consumer_claims:
        with pytest.raises(ValueError, match='delivery_unknown_requires_check'):
            inbox.deliver(tmp_path, [item], run_id='test', day=DAY)
        assert not (tmp_path / item['name']).exists()
    else:
        report = inbox.deliver(tmp_path, [item], run_id='test', day=DAY)
        assert report['delivered'] == 0 and report['already_delivered'] == 1


def test_identical_existing_file_is_not_requeued_if_consumer_moves_it(tmp_path, monkeypatch):
    item = payload()
    dest = tmp_path / item['name']
    dest.write_bytes(item['data'])
    read_bytes = Path.read_bytes

    def consumer_move(path):
        data = read_bytes(path)
        if path == dest:
            dest.unlink()
        return data

    monkeypatch.setattr(Path, 'read_bytes', consumer_move)
    report = inbox.deliver(tmp_path, [item], run_id='test', day=DAY)
    assert report['already_delivered'] == 1 and report['delivered'] == 0
    assert not dest.exists()


def test_inbox_resolution_matches_factory(monkeypatch, tmp_path):
    monkeypatch.delenv('TEUM_HOME', raising=False)
    monkeypatch.delenv('TEUM_INBOX', raising=False)
    root = tmp_path / 'issue_collector'
    assert inbox.inbox_path(root) == tmp_path / 'script_agent/input'
    monkeypatch.setenv('TEUM_HOME', str(tmp_path / 'var'))
    assert inbox.inbox_path(root) == tmp_path / 'var/script_agent/input'
    monkeypatch.setenv('TEUM_INBOX', str(tmp_path / 'factory-input'))
    assert inbox.inbox_path(root) == tmp_path / 'factory-input'
    assert inbox.inbox_path(root, tmp_path / 'explicit') == tmp_path / 'explicit'


def test_factory_command_works_from_other_cwd_with_spaces(tmp_path):
    dest = tmp_path / 'factory input'
    template = f'"{ROOT}/collect_to_inbox.sh" --limit 5 --inbox "{{inbox}}" --date 2026-11-11 --dry-run'
    command = [arg.replace('{inbox}', str(dest)) for arg in shlex.split(template)]
    env = {**os.environ, 'ISSUE_COLLECTOR_PYTHON': sys.executable}
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['inbox'] == str(dest)
    assert report['requested_counts'] == {'issue': 2, 'market': 3}
    assert report['status'] == 'planned' and not dest.exists()


def test_actual_factory_collect_hook_accepts_documented_command(tmp_path, monkeypatch):
    factory_src = ROOT.parent / 'factory/src'
    if not factory_src.exists():
        pytest.skip('optional adjacent factory integration')
    monkeypatch.syspath_prepend(str(factory_src))
    from teum_factory.config import Config
    from teum_factory import steps

    monkeypatch.setenv('TEUM_HOME', str(tmp_path / 'factory home'))
    monkeypatch.setenv('ISSUE_COLLECTOR_PYTHON', sys.executable)
    monkeypatch.delenv('TEUM_COLLECT_CMD', raising=False)
    monkeypatch.delenv('TEUM_COLLECT_INTERVAL', raising=False)
    home = tmp_path / 'factory home'
    home.mkdir()
    # Exact teum.env quoting from INTEGRATION.md, plus no-API verification flag.
    (home / 'teum.env').write_text(
        f'TEUM_COLLECT_CMD=\'bash "{ROOT}/collect_to_inbox.sh" --limit 5 --inbox "{{inbox}}" --dry-run\'\n'
        'TEUM_COLLECT_INTERVAL=86400\n')
    cfg = Config.load()
    cfg.work.mkdir(parents=True)
    (cfg.sa_root / 'input').mkdir(parents=True)
    real_run = steps.run_cmd
    captured = []

    def record_run(*args, **kwargs):
        result = real_run(*args, **kwargs)
        captured.append((args, kwargs, result))
        return result

    monkeypatch.setattr(steps, 'run_cmd', record_run)
    assert steps.collect(cfg, False)['collector_exit'] == 0
    args, kwargs, (rc, out, err) = captured[0]
    assert args[1] == 10800 and kwargs['env']['TEUM_INBOX'] == str(cfg.sa_root / 'input')
    assert json.loads(out)['inbox'] == str(cfg.sa_root / 'input')
    assert json.loads(out)['requested_counts'] == {'issue': 2, 'market': 3}
    assert steps.collect(cfg, False)['next_collect_in_sec'] > 0
    assert len(captured) == 1


def test_run_cli_reports_single_json_without_progress_on_stdout(tmp_path, collected, monkeypatch, capsys):
    from issue_collector import cli
    root = tmp_path / 'collector'
    root.mkdir()
    (root / 'config.toml').write_text('[collection]\nstream_workers = 2\n')
    monkeypatch.setattr(sys, 'argv', ['issue-collector', '--root', str(root), 'run',
        '--date', DAY.isoformat(), '--limit', '5', '--inbox', str(tmp_path / 'input')])
    assert cli.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report['delivered'] == 5 and report['status'] == 'complete'


def test_actual_stream_stops_extracting_at_quota(tmp_path, monkeypatch):
    """More valid topics are available; don't extract/export then discard extras."""
    topics, candidates, extractions = [], [], []
    for n in range(5):
        ext, _, articles = sample()
        ids = [f'a-{n}', f'b-{n}']
        for i, article in enumerate(articles):
            article.update(id=ids[i], url=f'https://www.hani.co.kr/arti/{n}-{i}.html',
                           title='공식 발표', publisher=f'편집국 {i}')
            ext.facts[0].evidence[i].article_id = ids[i]
        candidates.extend(articles)
        topics.append(Topic(id=f'topic-{n}', title='공식 발표', reason='영향', importance=4,
                            candidate_ids=ids, search_query='발표'))

    class Models:
        def __init__(self, *args): pass

        def call(self, stage, data, schema):
            if stage == 'select':
                return Selection(topics=topics, coverage_notes=[])
            ext, ver, _ = sample()
            ids = [a['id'] for a in data['articles']]
            for evidence, article_id in zip(ext.facts[0].evidence, ids):
                evidence.article_id = article_id
            if stage == 'extract':
                extractions.append(data['topic']['id'])
                return ext
            ver.topic_confirmed = True
            ver.central_fact_ids = ['F1']
            ver.checks[0].supported_article_ids = ids
            return ver

    monkeypatch.setattr(pipeline, 'Models', Models)
    monkeypatch.setattr(pipeline, 'discover', lambda *a: candidates)
    monkeypatch.setattr(pipeline, 'acquire', lambda fetcher, article, *a: article)
    monkeypatch.setattr(pipeline, 'search_news', lambda *a, **kw: [])
    cfg = {'collection': {'http_timeout_sec': 1, 'candidate_topics': 5, 'max_articles_per_topic': 8,
           'http_workers': 2, 'min_independent_sources': 2, 'min_facts': 1, 'google_search': False},
           'script': {'target_sec': 60}}
    result = pipeline.collect_stream(tmp_path, cfg, {}, DAY, 'issue', tmp_path / 'run', 2)
    assert result['status'] == 'complete' and len(result['items']) == 2
    assert extractions == ['topic-0', 'topic-1']
    assert len(list((tmp_path / 'run/sources').glob('*.md'))) == 2
