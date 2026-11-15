"""Offline privacy regressions: malicious responses must not reach sinks."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest

from source_collector.models import CandidateSource
from source_collector.privacy import privacy_codes, PrivacyViolation, require_safe_candidate
from source_collector.generator import SourceGenerator
from source_collector.prescreen import Deduplicator
from source_collector.judge import HeadlessJudge
from source_collector.writer import SourceWriter
from source_collector.ledger import LedgerManager
from source_collector.harness import CollectionHarness
from source_collector.cli import main

@pytest.fixture
def candidate():
    return CandidateSource(id='test-C01-01', category='C01', title='동료와의 하루',
        url='https://www.instagram.com/p/DFixturePrivacy/', author='익명',
        source_id_ref='SRC-021', source_name='사연', production_type='창작·각색형',
        license_kind='unknown', license_info={'kind': 'unknown'}, attribution='익명',
        summary='동료 이야기', text='동료와 이야기를 나눴다. ' * 60, batch_id='test')

@pytest.mark.parametrize('text', [
    '@fixture_account', '＠fixture_account', '@\u200bfixture_account',
    '%40fixture_account', '&#64;fixture_account', '계정명: fixture_account',
    '인스타그램 아이디는 fixture_account', '인스타: fixture_account',
    '닉네임: 행복한고양이', 'username: fixture_account',
    'https://www.instagram.com/fixture_account/',
    'instagram.com/fixture_account', 'https://m.instagram.com/fixture_account/',
    'https://www.instagram.com/stories/fixture_account/123/',
    'https://www.instagram.com/p/DFixture/?igsh=abc',
    'https://threads.net/@fixture_account', 'https://x.com/fixture_account',
    'fixture@example.com', 'fixture @ example.com',
    '010-1234-5678', '01012345678', '+82 10 1234 5678', '02-123-4567',
    '900101-1234567',
])
def test_detect_encoded_and_contextual_identifiers(text):
    assert privacy_codes(text)

@pytest.mark.parametrize('text', ['NASA', '제임스 랜디', '1978년 1,200명 42.8%',
    'https://www.instagram.com/p/DFixture/', 'https://www.instagram.com/reel/DFixture/',
    'https://www.nasa.gov/missions/webb/', '동료와 친구'])
def test_public_source_details_allowed(text):
    assert not privacy_codes(text)

@pytest.mark.parametrize('field', ['title', 'text', 'summary', 'author', 'url',
    'source_name', 'attribution', 'eval_reason', 'license_info', 'id'])
def test_all_sinks_reject_before_any_write(candidate, tmp_path, field):
    value = {'evidence': [{'contact': '@fixture_account'}]} if field == 'license_info' else '@fixture_account'
    bad = replace(candidate, **{field: value})
    writer = SourceWriter(tmp_path/'out', tmp_path/'lib')
    ledger = LedgerManager([tmp_path/'rows.csv'], tmp_path/'lib')
    for action in [lambda: writer.save(bad), lambda: ledger.append_to_csvs([candidate, bad]),
                   lambda: ledger.write_collection_log('test', [bad], tmp_path)]:
        with pytest.raises(PrivacyViolation) as error:
            action()
        assert 'fixture_account' not in str(error.value)
        assert not list(tmp_path.rglob('*'))


def test_unmarked_story_author_and_nested_account_field(candidate):
    with pytest.raises(PrivacyViolation):
        require_safe_candidate(replace(candidate, author='행복한고양이'))
    assert privacy_codes({'extra': {'username': 'plain_name'}})


def test_generator_rejects_unsafe_raw_response_before_dedup(candidate, tmp_path, monkeypatch):
    dedup = Deduplicator([], tmp_path/'absent')
    gen = SourceGenerator(dedup, mock=False)
    replies = iter([[{'title': candidate.title, 'text': candidate.text, 'author': 'plain_name'}],
                    [{'title': candidate.title, 'text': candidate.text + ' @fixture_account', 'author': '익명'}],
                    [{'title': candidate.title, 'text': candidate.text, 'author': '익명', 'url': candidate.url}]])
    monkeypatch.setattr(gen, '_request_candidates_from_llm', lambda **kw: next(replies))
    result = gen.generate_for_category('C01', 1, 'test')
    assert len(result) == 1 and result[0].author == '익명'
    assert gen.privacy_rejections == 2
    assert len(dedup.existing_ids) == 1


def test_rewrite_blocked_before_next_api_request(candidate, monkeypatch):
    judge = HeadlessJudge()
    calls = []
    def api(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=('SCORE: 7 | REASON: 분량 부족' if len(calls) == 1
                               else candidate.text + ' @fixture_account'))
    monkeypatch.setattr('source_collector.judge.subprocess.run', api)
    with pytest.raises(PrivacyViolation):
        judge.evaluate_and_ensure(candidate)
    assert len(calls) == 2


def test_judge_reason_and_input_rejected(candidate, monkeypatch):
    calls = []
    def api(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout='SCORE: 10 | REASON: @fixture_account 훌륭함')
    monkeypatch.setattr('source_collector.judge.subprocess.run', api)
    with pytest.raises(PrivacyViolation):
        HeadlessJudge().evaluate(replace(candidate, title='@fixture_account'))
    assert not calls
    with pytest.raises(PrivacyViolation):
        HeadlessJudge().evaluate(candidate)


def test_cli_checks_unused_frontmatter_without_echo(tmp_path, capsys):
    p = tmp_path/'input.md'
    p.write_text('---\ncategory: C01\nnotes: 연락 fixture@example.com\n---\n# 제목\n본문')
    before = p.read_bytes()
    assert main(['evaluate', str(p), '--mock', '--elevate']) == 2
    out = capsys.readouterr().out
    assert 'privacy_blocked' in out and 'fixture@example.com' not in out
    assert p.read_bytes() == before


def test_harness_isolates_judge_privacy_failure(candidate, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr('source_collector.harness.Deduplicator', lambda: Deduplicator([], tmp_path/'absent'))
    h = CollectionHarness(mode='sandbox', categories=['C01'], count_per_category=1,
        output_dir=tmp_path/'out', csv_paths=[tmp_path/'rows.csv'],
        source_library_root=tmp_path/'lib', mock=True)
    monkeypatch.setattr(h.generator, 'generate_for_category', lambda *a, **kw: [replace(candidate)])
    def unsafe(c):
        c.eval_reason = '@fixture_account'
        c.score = 10
        return c
    monkeypatch.setattr(h.judge, 'evaluate_and_ensure', unsafe)
    result = h.run()
    assert result['total_accepted'] == 0 and result['privacy_rejections'] == 3
    assert not list((tmp_path/'out').glob('*.md'))
    assert not (tmp_path/'rows.csv').exists()
    assert 'fixture_account' not in capsys.readouterr().out
    assert 'fixture_account' not in Path(result['collection_log']).read_text()


def test_generation_prompts_include_policy_and_omit_legacy_private_titles(tmp_path, monkeypatch):
    dedup = Deduplicator([], tmp_path/'absent')
    dedup.existing_titles.add('@fixture_account 과거 제목')
    prompts = []
    def api(args, **kwargs):
        prompts.append(args[-1])
        return SimpleNamespace(stdout='[]')
    monkeypatch.setattr('source_collector.generator.subprocess.run', api)
    gen = SourceGenerator(dedup)
    for cat, story in [('C01', True), ('C13', False)]:
        gen._request_candidates_from_llm(cat, '시험', 1, story, 1, 'test')
    assert len(prompts) == 2
    assert all('[개인정보 금지' in p and '@fixture_account' not in p for p in prompts)
    assert '"author": "익명"' in prompts[0]


def test_file_validation_blocks_before_subprocess(tmp_path, monkeypatch):
    p = tmp_path/'private.md'
    p.write_text('notes: @fixture_account')
    def forbidden(*args, **kwargs):
        pytest.fail('unsafe file forwarded to validator')
    monkeypatch.setattr('source_collector.writer.subprocess.run', forbidden)
    valid, info = SourceWriter(tmp_path).validate_file(p)
    assert not valid and 'privacy_blocked' in info['error']
    assert 'fixture_account' not in str(info)
