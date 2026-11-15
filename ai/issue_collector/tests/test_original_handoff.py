"""Full original preservation across the independently installable file boundary."""
import copy
import importlib.abc
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import date

import httpx
import pytest

from issue_collector.core import Topic, digest
from issue_collector.handoff import export_source
from test_quality import sample

AGENT = Path(__file__).resolve().parents[2] / 'script_agent'


def export(tmp_path, *, long=False):
    extraction, _, articles = sample()
    for i, article in enumerate(articles):
        article.update(url=f'https://example.org/article-{i}', title=f'시험 원문 {i}', publisher=f'시험 편집국 {i}')
        article['text'] += '\n\n추가 문단과 **서식**을 보존합니다.\n```\n본문 속 코드입니다.\n```\n'
        if long:
            article['text'] += '긴 원문 마지막까지 보존합니다.\n' * 1800
        article['body_sha256'] = digest(article['text'])
    topic = Topic(id='originals', title='제도 변경 발표', reason='시험', importance=4,
                  candidate_ids=['a', 'b'], search_query='시험')
    path = export_source(tmp_path, 'issue', topic, [f.model_dump() for f in extraction.facts],
                         articles, date(2026, 11, 11), 60)
    return path, articles, extraction


def test_single_file_can_be_moved_without_collector_sidecars(tmp_path):
    from script_agent.io import read_source, load_config
    from script_agent.news_context import original_bodies
    source, articles, _ = export(tmp_path / 'collector', long=True)
    moved = tmp_path / 'standalone.md'
    shutil.copy2(source, moved)
    shutil.rmtree(source.parent)
    text, meta, _ = read_source(moved, load_config(AGENT)['pipeline']['max_source_chars'])
    assert len(text) > 60000
    assert original_bodies(text) == [a['text'] for a in articles]
    assert [s.source_url for s in meta.original_sources] == [a['url'] for a in articles]
    assert meta.license.kind == 'unknown'
    assert '© 틈(TEUM) 팀' in meta.notes
    assert '재구성' in meta.notes


def test_missing_truncated_or_mismatched_original_rejected(tmp_path):
    _, articles, extraction = export(tmp_path / 'seed')
    topic = Topic(id='bad', title='시험', reason='시험', importance=1, candidate_ids=['a'], search_query='시험')
    for change in ({'text': ''}, {'truncated': True}, {'body_sha256': 'bad'}):
        bad = copy.deepcopy(articles)
        bad[0].update(change)
        with pytest.raises(ValueError):
            export_source(tmp_path / 'bad', 'issue', topic, [f.model_dump() for f in extraction.facts],
                          bad, date(2026, 11, 11), 60)
        assert not (tmp_path / 'bad').exists()


def test_collection_imports_and_export_without_generator(tmp_path):
    # A hard import blocker catches lazy imports too, even in this shared dev environment.
    code = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == 'script_agent' or fullname.startswith('script_agent.'):
            raise AssertionError('collector imported generator: '+fullname)
sys.meta_path.insert(0, Block())
from issue_collector import pipeline, llm, cli
from issue_collector.core import Topic
from datetime import date
from pathlib import Path
article = dict(id='a', title='시험', publisher='시험', url='https://example.org/a', text='실제 보존된 원문 본문입니다.')
fact = dict(id='F1', text='검증 요약입니다.', attribution='기관', event_date='2026-11-11', kind='event', evidence=[dict(article_id='a', quote=article['text'])])
pipeline.export_source(Path(sys.argv[1]), 'issue', Topic(id='test', title='시험', reason='시험', importance=1, candidate_ids=['a'], search_query='시험'), [fact], [article], date(2026,11,11), 60)
assert not any(name.startswith('script_agent') for name in sys.modules)
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    subprocess.run([sys.executable, '-c', code, str(tmp_path / 'export')], env=env, check=True, capture_output=True)
    assert len(list((tmp_path / 'export').glob('*.md'))) == 1


@pytest.fixture
def flow(tmp_path, monkeypatch):
    from script_agent.io import load_config, read_source
    from script_agent.models import Material, Draft, Review, RevisionResult
    from script_agent.pipeline import Pipeline
    from script_agent.providers import Completion
    monkeypatch.setattr(httpx, 'post', lambda *a, **kw: pytest.fail('unexpected network'))
    source, articles, extraction = export(tmp_path / 'collected')
    text, meta, sha = read_source(source)
    root = tmp_path / 'agent'
    shutil.copytree(AGENT / 'prompts', root / 'prompts')
    config = load_config(AGENT)
    config['pipeline']['max_revisions'] = 1
    material = Material(production_type='사실해설형', category='C16', routing_reason='시험', summary='제도 발표',
        conflict='', details=[], question='무엇이 달라집니까?', unknowns=[], outline=['발표'],
        facts=[dict(id='f1', text='제도 변경을 발표했다.', quote=extraction.facts[0].evidence[0].quote,
                    kind='source_statement', conditions=['F1 / a'])])
    draft = Draft(title='정부 제도 변경 발표', format='정보형', tags=['정책'],
        script=['정부가 제도 변경 내용을 공개했습니다.', '시행 시점은 내년으로 정해졌습니다.'],
        claims=[dict(sentence=i, kind='source_statement', fact_ids=['f1']) for i in (1, 2)],
        adaptation_note={'from_source': [], 'new': []}, unresolved=[], changes=[])
    payloads = []
    class Fake:
        def complete(self, system, payload, schema):
            payloads.append(copy.deepcopy(payload))
            value = {'Material': material, 'Draft': draft,
                     'RevisionResult': RevisionResult(material=None, draft=draft),
                     'Review': Review(verdict='pass', issues=[], scores=dict(grounding=4,listening=4,engagement=4,type_fit=4), strengths=[],remaining=[])}[schema.__name__]
            return Completion(value, {}, 0, 'offline-test')
    pipe = Pipeline(root, config, {}, providers={'generator': Fake(), 'reviewer': Fake()})
    return source, articles, text, meta, sha, pipe, payloads, material


def test_direct_cli_and_feedback_read_and_embed_originals(flow, tmp_path, monkeypatch, capsys):
    from script_agent import cli
    from script_agent.models import Handoff, Issue
    source, articles, text, meta, sha, pipe, payloads, _ = flow
    before = source.read_bytes()
    monkeypatch.setattr(cli, 'load_config', lambda root: pipe.config)
    monkeypatch.setattr(cli, 'credentials', lambda *a, **kw: {})
    monkeypatch.setattr(cli, 'Pipeline', lambda *a, **kw: pipe)
    destination = tmp_path / 'generated'
    monkeypatch.setattr(sys, 'argv', ['script-agent', '--root', str(pipe.root), 'generate', str(source), '--destination', str(destination)])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    path = Path(result['output'])
    out = json.loads(path.read_text())
    Handoff.model_validate(out)
    assert source.read_bytes() == before
    assert out['sources'][0]['text'] == text
    assert out['sources'][0]['sha256'] == digest(text)
    assert (path.parent / out['sources'][0]['text_path']).resolve() == source.resolve()
    assert out['license'] == 'All rights reserved'
    assert any('© 틈(TEUM) 팀' in n for n in out['rights_notes'])
    assert out['input_metadata']['license']['kind'] == 'unknown'
    assert out['generator']['revision'] == 1
    assert out['status'] == 'hold'  # no claim of quality/rights approval from mocked output
    assert all(p['source_text'] == text for p in payloads)
    assert all(a['text'] in p['source_text'] for a in articles for p in payloads)
    source.unlink()  # feedback must be self-contained in the output JSON
    revised = pipe.apply_feedback(path, [Issue(sentence=1, problem='시험', reason='시험', direction='시험')], out['version'])
    assert revised['sources'][0]['text'] == text
    assert revised['license'] == 'All rights reserved'
    assert revised['generator']['feedback_revision'] == 1
    assert revised['generator']['revision'] == 1
    assert all(p['source_text'] == text for p in payloads)


def test_quotes_must_come_from_original_bodies(flow):
    from script_agent.validation import validate_material
    _, _, text, meta, _, _, _, material = flow
    validate_material(material, text, meta)
    material.facts[0].quote = '정부가 십일월 십일일 제도 변경을 알렸다.'
    with pytest.raises(ValueError, match='fact_quote_not_in_original_article'):
        validate_material(material, text, meta)


@pytest.mark.parametrize('mode', ['standard', 'batch'])
def test_queue_is_self_contained_and_embeds_originals(flow, monkeypatch, mode):
    from script_agent.queue import process_queue
    from script_agent.batch import GeminiBatchClient
    source, articles, text, meta, sha, pipe, payloads, material = flow
    pending = pipe.root / 'input'
    pending.mkdir()
    shutil.copy2(source, pending / source.name)
    if mode == 'batch':
        from script_agent.models import Draft
        from script_agent.providers import APIProvider
        requests = {}
        def create(self, model, jobs, display_name):
            key = str(len(requests)); requests[key] = jobs; return key
        def poll(self, key, **kwargs):
            result = []
            for req in requests[key]:
                payload = json.loads(req['request']['contents'][0]['parts'][0]['text'])
                assert payload['source_text'] == text
                if 'feedback' in payload:
                    value = {'material': None, 'draft': payload['draft']}
                elif 'material' in payload:
                    value = Draft(title='정부 제도 발표', format='정보형', tags=[], script=['정부가 변경 내용을 공개했습니다.'],
                        claims=[dict(sentence=1, kind='source_statement', fact_ids=['f1'])],
                        adaptation_note={'from_source': [], 'new': []}, unresolved=[], changes=[]).model_dump()
                else:
                    value = material.model_dump()
                result.append({'metadata': req['metadata'], 'response': {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(value, ensure_ascii=False)}]}}]}})
            return {'state': 'BATCH_STATE_SUCCEEDED', 'response': {'inlinedResponses': {'inlinedResponses': result}}}
        monkeypatch.setattr(GeminiBatchClient, 'create_batch', create)
        monkeypatch.setattr(GeminiBatchClient, 'poll_batch', poll)
        pipe.providers['generator'] = APIProvider(pipe.config['models']['generator'], {'gemini_api_key': 'test-only'})
    result = process_queue(pipe.root, pipe, mode=mode)
    assert result[0]['status'] == 'completed', result
    out = json.loads(next((pipe.root / 'output').rglob('S2-*.json')).read_text())
    assert out['sources'][0]['text'] == text
    archive = pipe.root / 'archive' / sha / source.name
    assert archive.read_bytes() == source.read_bytes()
    assert not (pipe.root.parent / 'source_collector').exists()
    assert all(a['text'] in out['sources'][0]['text'] for a in articles)


def test_original_tampering_or_partial_article_block_is_rejected(tmp_path):
    from script_agent.io import read_source
    path, _, _ = export(tmp_path)
    original = path.read_text()
    for corrupted in (original.replace('추가 문단과 **서식**', '바뀐 문단과 **서식**'),
                      original.rsplit('````', 1)[0]):
        path.write_text(corrupted)
        with pytest.raises(ValueError, match='invalid_news_originals'):
            read_source(path)


def test_direct_cli_never_overwrites_completed_json(flow, tmp_path, monkeypatch, capsys):
    from script_agent import cli
    source, _, _, _, sha, pipe, payloads, _ = flow
    destination = tmp_path / 'existing'
    destination.mkdir()
    output = destination / ('S2-' + sha[:16] + '.json')
    output.write_text('{"previous_feedback":"preserve"}')
    monkeypatch.setattr(cli, 'load_config', lambda root: pipe.config)
    monkeypatch.setattr(cli, 'credentials', lambda *a, **kw: {})
    monkeypatch.setattr(cli, 'Pipeline', lambda *a, **kw: pipe)
    monkeypatch.setattr(sys, 'argv', ['script-agent', '--root', str(pipe.root), 'generate', str(source), '--destination', str(destination)])
    assert cli.main() == 2
    assert json.loads(capsys.readouterr().err)['code'] == 'output_exists_use_new_destination'
    assert json.loads(output.read_text()) == {'previous_feedback': 'preserve'}
    assert not payloads


def test_article_limit_rejects_instead_of_truncating(monkeypatch):
    from issue_collector import acquisition
    from types import SimpleNamespace
    article = {'text': '원문 보존 대상 문장입니다. ' * 30, 'published_at': '2026-11-11T10:00:00+09:00'}
    events = []
    monkeypatch.setattr(acquisition, 'candidate', lambda *a: {})
    monkeypatch.setattr(acquisition, 'extract_article', lambda *a: article)
    fetcher = SimpleNamespace(get=lambda url: (url, '<html>test</html>'),
                              event=lambda *a, **kw: events.append(kw))
    result = acquisition.acquire(fetcher, {'id': 'a', 'url': 'https://example.org/a', 'title': '시험', 'method': 'test'},
                                 date(2026, 11, 11), {'max_article_chars': 200})
    assert result is None
    assert events[-1]['code'] == 'article_body_too_large'
    assert len(article['text']) > 200 and 'truncated' not in article
