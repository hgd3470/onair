import json
import unicodedata

import pytest

from script_agent.io import atomic_json
from script_agent.models import Claim, Fact, Handoff, License, Material, SourceMeta
from script_agent.pipeline import parse_feedback
from script_agent.rights import complete_license, resolve_rights
from script_agent.validation import checks
from test_pipeline import ROOT, fixture, flow_draft, review, setup_pipeline


def problems(line, text='이것은 독립적으로 작성한 시험용 원문입니다.'):
    c = fixture()
    draft = flow_draft()
    draft.script = [line]
    draft.claims = [Claim(sentence=1, kind='transition', fact_ids=[])]
    return checks(draft, Material.model_validate(c['material']), text, SourceMeta.model_validate(c['metadata']))


@pytest.mark.parametrize('length,expected', [(39, False), (40, False), (41, True)])
def test_sentence_limit_counts_spaces_punctuation_and_nfc(length, expected):
    line = '가 ' + '나' * (length - 3) + '.'
    for form in ('NFC', 'NFD'):
        issues = problems(unicodedata.normalize(form, line))
        assert any(i.problem == '문장 40자 초과' for i in issues) == expected


@pytest.mark.parametrize('line', ['20메가와트입니다.', '9급입니다.', 'AI를 씁니다.', '３명입니다.', 'ＡＩ예요.', '3.5%예요.'])
def test_spoken_script_rejects_digits_and_english(line):
    assert any(i.problem == '숫자·영문 한글 읽기 필요' for i in problems(line))


@pytest.mark.parametrize('line', ['이십 메가와트입니다.', '구 급입니다.', '에이아이를 씁니다.', '삼 점 오 퍼센트입니다.', '세 명이에요.'])
def test_spoken_korean_forms_are_accepted(line):
    assert not any(i.problem == '숫자·영문 한글 읽기 필요' for i in problems(line))


@pytest.mark.parametrize('line', [
    '이 가상 시설의 발전 용량은 이십 메가와트입니다.',
    '이 가상 시설의 발전 용량은 이십 메가와트였어요.',
    '이 가상 시설의 발전 용량은 이십 메가와트',
])
def test_source_copy_near_copy_and_split_copy_are_flagged(line):
    source = '이 가상 시설의 발전 용량은 이십 메가와트입니다.'
    assert any(i.problem == '원문 표현 재서술 필요' for i in problems(line, source))


def test_independent_explanation_and_short_common_phrase_are_not_copy_flags():
    assert not any(i.problem == '원문 표현 재서술 필요' for i in problems(
        '얼마나 생산할까요?', '이 가상 시설의 발전 용량은 이십 메가와트입니다.'))
    assert not any(i.problem == '원문 표현 재서술 필요' for i in problems('고맙습니다.', '고맙습니다.'))


def test_rules_trigger_revision_even_if_model_passes_and_preserve_source(tmp_path):
    corrected = flow_draft()
    corrected.script[0] = '이십 메가와트는 어느 정도일까요?'
    pipe, c, draft, generator, reviewer = setup_pipeline(tmp_path, [review('pass'), review('pass')], [corrected])
    draft.script[0] = '20메가와트는 어느 정도일까요?'
    out = pipe.run(c['source_text'], SourceMeta.model_validate(c['metadata']), '3'*64, tmp_path/'out')
    assert out['script'][0] == corrected.script[0]
    assert out['generator']['revision'] == 1
    assert out['status'] == 'ready_for_downstream_review'
    assert out['self_review'][0]['verdict'] == 'revise'
    assert out['sources'][0]['text'] == c['source_text']
    assert out['material']['facts'] == c['material']['facts']
    assert generator.payloads[-1]['feedback']['issues'][0]['sentence'] == 1


def test_reading_conflict_holds_without_rewriting_original(tmp_path):
    pipe, c, draft, generator, reviewer = setup_pipeline(tmp_path, [review('pass')])
    text = '20메가와트입니다.'
    meta = SourceMeta.model_validate(c['metadata'])
    meta.production_type = '원문낭독형'
    meta.edition = '자체 작성 시험판'
    material = Material.model_validate(c['material'])
    material.production_type = '원문낭독형'
    material.facts = [Fact(id='f1', text=text, quote=text, kind='source_statement', conditions=[])]
    draft.script = [text]
    draft.claims = [Claim(sentence=1, kind='quotation', fact_ids=['f1'])]
    generator.values = [material, draft]
    out = pipe.run(text, meta, '4'*64, tmp_path/'out')
    assert out['status'] == 'hold'
    assert out['script'] == [text]
    assert out['self_review'][-1]['verdict'] == 'needs_evidence'
    assert out['generator']['revision'] == 0


@pytest.mark.parametrize('code,flags', [
    ('BY', (True, True, False)), ('BY-SA', (True, True, True)),
    ('BY-NC', (False, True, False)), ('BY-ND', (True, False, False)),
    ('BY-NC-SA', (False, True, True)), ('BY-NC-ND', (False, False, False)),
])
def test_explicit_cc_profile_completes_missing_flags(code, flags):
    original = License(kind=f'CC {code} 4.0')
    lic, notes, conflicts = complete_license(original)
    assert (lic.commercial, lic.modify, lic.share_alike) == flags
    assert lic.version == '4.0' and lic.url.endswith('/4.0/')
    assert notes and not conflicts
    assert lic.evidence is None and original.commercial is None


def test_canonical_url_can_identify_license_but_cannot_prove_applicability():
    meta = SourceMeta(license=License(url='https://creativecommons.org/licenses/by/4.0/'))
    resolved, notes, status = resolve_rights(meta)
    assert resolved.license.kind == 'CC BY 4.0'
    assert status == 'unverified'
    assert any('evidence' in note for note in notes)


@pytest.mark.parametrize('url', ['https://creativecommons.org.evil.test/licenses/by/4.0/',
                               'https://example.org/article', 'https://creativecommons.org/licenses/by/3.0/'])
def test_arbitrary_urls_do_not_grant_permissions(url):
    lic, _, _ = complete_license(License(url=url))
    assert lic.kind == 'unknown' and lic.commercial is None


def test_conflicting_conditions_are_not_overwritten_or_ready():
    meta = SourceMeta(license=License(kind='CC BY-NC 4.0', commercial=True, evidence='입력 확인'), attribution='작성자')
    resolved, notes, status = resolve_rights(meta)
    assert resolved.license.commercial is True
    assert status == 'unverified'
    assert any('불일치' in note for note in notes)


@pytest.mark.parametrize('license', [
    License(kind='CC BY 4.0', version='3.0'),
    License(kind='CC BY 4.0', url='https://creativecommons.org/licenses/by-nc/4.0/'),
    License(kind='CC BY 4.0', url='https://example.org/license'),
])
def test_conflicting_identifiers_remain_unverified(license):
    assert resolve_rights(SourceMeta(license=license))[2] == 'unverified'


def test_user_summary_requires_original_source_conditions():
    meta = SourceMeta.model_validate(fixture()['metadata'] | {'source_kind': 'user_summary'})
    assert resolve_rights(meta)[2] == 'unverified'
    meta = SourceMeta.model_validate(meta.model_dump() | {'original_sources': [
        {'title': '원래 기사', 'source_url': 'https://example.org/article', 'license': {'kind': 'unknown'}}]})
    assert resolve_rights(meta)[2] == 'unverified'
    assert any('original_sources[0].license' in note for note in resolve_rights(meta)[1])


def test_resolved_license_in_handoff_raw_metadata_preserved(tmp_path):
    pipe, c, _, _, _ = setup_pipeline(tmp_path, [review('pass')])
    meta = SourceMeta.model_validate(c['metadata'])
    meta.license = License(kind='CC BY 4.0', evidence='자체 시험용 조건 선언')
    raw = meta.model_dump()
    out = pipe.run(c['source_text'], meta, '5'*64, tmp_path/'out')
    Handoff.model_validate(out)
    assert out['sources'][0]['license']['commercial'] is True
    assert out['input_metadata'] == raw
    assert out['input_metadata']['license']['commercial'] is None
    assert out['rights_notes'] and out['status'] == 'ready_for_downstream_review'


def envelope(script_id='S2-test', decision='revise'):
    return {'script_id': script_id, 'round': 1, 'decision': decision, 'items': [
        {'stage': 'R5a', 'sent': 1, 'sentence': 1, 'problem': '숫자 표기',
         'reason': '영문 포함', 'fix': '한글 읽기', 'direction': '한글 읽기', 'category': 'listening'}]}


def test_refine_jsonl_selects_only_target_and_preserves_core_keys(tmp_path):
    path = tmp_path/'revise.jsonl'
    path.write_text('\ufeff' + '\n'.join(json.dumps(envelope(cid)) for cid in ['S2-other', 'S2-test']))
    assert parse_feedback(path, 'S2-test')[0].model_dump() == {
        'sentence': 1, 'problem': '숫자 표기', 'reason': '영문 포함', 'direction': '한글 읽기'}
    with pytest.raises(ValueError, match='missing_or_duplicate'):
        parse_feedback(path, 'S2-missing')
    path.write_text('\n'.join([json.dumps(envelope())]*2))
    with pytest.raises(ValueError, match='missing_or_duplicate'):
        parse_feedback(path, 'S2-test')


def test_refine_human_review_and_conflicting_aliases_are_not_applied(tmp_path):
    path = tmp_path/'revise.jsonl'
    path.write_text(json.dumps(envelope(decision='human_review')))
    with pytest.raises(ValueError, match='requires_human_review'):
        parse_feedback(path, 'S2-test')
    row = envelope()
    row['items'][0]['sent'] = 2
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match='alias_conflict'):
        parse_feedback(path, 'S2-test')


@pytest.mark.parametrize('data', [[], {}, [1], 'bad', [{'items': []}]])
def test_malformed_feedback_is_rejected(tmp_path, data):
    path = tmp_path/'feedback.json'
    atomic_json(path, data)
    with pytest.raises(ValueError):
        parse_feedback(path, 'S2-test')


def test_internal_and_external_revisions_have_separate_budgets_with_jsonl(tmp_path):
    pipe, c, _, _, _ = setup_pipeline(tmp_path, [review(), review('pass'), review('pass'), review('pass')], [flow_draft(), flow_draft(), flow_draft()])
    out = pipe.run(c['source_text'], SourceMeta.model_validate(c['metadata']), '6'*64, tmp_path/'out')
    assert out['generator']['revision'] == 1
    path = tmp_path/'out'/f"{out['content_id']}.json"
    feedback = tmp_path/'revise.jsonl'
    feedback.write_text(json.dumps(envelope(out['content_id'])))
    issues = parse_feedback(feedback, out['content_id'])
    revised = pipe.apply_feedback(path, issues, out['version'])
    assert revised['generator']['revision'] == 1 and revised['version'] == '1.0.2'
    assert revised['generator']['feedback_revision'] == 1
    revised = pipe.apply_feedback(path, issues, revised['version'])
    assert revised['generator']['revision'] == 1 and revised['version'] == '1.0.3'
    assert revised['generator']['feedback_revision'] == 2
    with pytest.raises(ValueError, match='revision_budget_exhausted'):
        pipe.apply_feedback(path, issues, revised['version'])


def test_published_schemas_match_models():
    for name, model in [('handoff', Handoff), ('input-metadata', SourceMeta)]:
        assert json.loads((ROOT/'schema'/f'{name}.schema.json').read_text()) == model.model_json_schema()


def test_deterministic_defect_cannot_pass_when_budget_is_zero(tmp_path):
    pipe, c, draft, _, _ = setup_pipeline(tmp_path, [review('pass')])
    pipe.config['pipeline']['max_revisions'] = 0
    draft.script[0] = '20메가와트는 어느 정도일까요?'
    out = pipe.run(c['source_text'], SourceMeta.model_validate(c['metadata']), '7'*64, tmp_path/'out')
    assert out['status'] == 'hold' and out['self_review'][-1]['verdict'] == 'revise'


def test_cli_jsonl_requires_reviewed_version_before_revision(tmp_path, monkeypatch, capsys):
    import shutil
    import sys
    from script_agent import cli
    pipe, c, _, generator, _ = setup_pipeline(tmp_path, [review('pass')])
    out = pipe.run(c['source_text'], SourceMeta.model_validate(c['metadata']), '8'*64, tmp_path/'out')
    path = tmp_path/'out'/f"{out['content_id']}.json"
    feedback = tmp_path/'revise.jsonl'
    feedback.write_text(json.dumps(envelope(out['content_id'])))
    shutil.copy2(ROOT/'config.toml', tmp_path/'config.toml')
    monkeypatch.setattr(cli, 'credentials', lambda *a, **k: {})
    monkeypatch.setattr(cli, 'Pipeline', lambda *a, **k: pipe)
    monkeypatch.setattr(sys, 'argv', ['script-agent', '--root', str(tmp_path), 'revise', str(path), str(feedback)])
    assert cli.main() == 2
    assert 'feedback_expected_version_required' in capsys.readouterr().err
    assert generator.calls == 2


@pytest.mark.parametrize('fields,expected', [
    ({'scope': 'script', 'sentence': None}, 0),
    ({'scope': 'script'}, 0),
    ({'scope': 'script', 'sentence': 0}, 0),
    ({'sentence': None}, 0),
    ({'sentence': 0}, 0),
    ({'scope': 'sentence', 'sentence': 2}, 2),
])
@pytest.mark.parametrize('wrapped', [False, True])
def test_feedback_scope_normalizes_global_and_preserves_sentences(tmp_path, fields, expected, wrapped):
    item = {'problem': '분량 검토', 'reason': '설명 보완 필요', 'direction': '근거 안에서 보완', **fields}
    data = envelope()
    data['items'] = [item]
    path = tmp_path / ('revise.jsonl' if wrapped else 'feedback.json')
    path.write_text(json.dumps(data if wrapped else [item]))
    before = path.read_bytes()
    notes = []
    assert parse_feedback(path, 'S2-test', normalization_notes=notes)[0].sentence == expected
    if fields.get('sentence') is None:
        assert notes == [{'item': 1, 'scope': fields.get('scope', 'legacy'),
                          'original_fields': {k: v for k, v in fields.items() if k == 'sentence'}, 'sentence': 0}]
    else:
        assert notes == []
    assert path.read_bytes() == before


@pytest.mark.parametrize('fields,code', [
    ({}, 'invalid_feedback_sentence'),
    ({'scope': 'sentence'}, 'invalid_feedback_sentence'),
    ({'scope': 'sentence', 'sentence': None}, 'invalid_feedback_sentence'),
    ({'scope': 'sentence', 'sentence': 0}, 'invalid_feedback_sentence'),
    ({'scope': 'script', 'sentence': 2}, 'feedback_scope_conflict'),
    ({'sentence': -1}, 'invalid_feedback_sentence'),
    ({'scope': 'script', 'sentence': -1}, 'invalid_feedback_sentence'),
    ({'sentence': '2'}, 'invalid_feedback_sentence'),
    ({'sentence': 2.0}, 'invalid_feedback_sentence'),
    ({'sentence': True}, 'invalid_feedback_sentence'),
    ({'sentence': False}, 'invalid_feedback_sentence'),
    ({'sentence': []}, 'invalid_feedback_sentence'),
    ({'scope': None, 'sentence': None}, 'invalid_feedback_scope'),
    ({'scope': [], 'sentence': None}, 'invalid_feedback_scope'),
    ({'scope': 'unknown', 'sentence': None}, 'invalid_feedback_scope'),
    ({'sentence': None, 'sent': 2}, 'feedback_alias_conflict'),
    ({'sentence': None, 'sent': 0}, 'feedback_alias_conflict'),
    ({'sentence': 1, 'sent': True}, 'invalid_feedback_sentence'),
])
def test_invalid_feedback_number_is_not_reinterpreted_as_global(tmp_path, fields, code):
    from script_agent.errors import safe_code
    data = envelope()
    data['items'] = [
        {'sentence': None, 'problem': '전체', 'reason': '전체', 'direction': '보완'},
        {'problem': '오류', 'reason': '번호 오류', 'direction': '확인', **fields},
    ]
    path = tmp_path/'revise.jsonl'
    path.write_text(json.dumps(data))
    notes = []
    with pytest.raises(ValueError, match=code) as error:
        parse_feedback(path, 'S2-test', normalization_notes=notes)
    assert safe_code(error.value) == code
    assert notes == []  # Invalid batches do not leave a partial normalization report.


def test_out_of_range_feedback_stops_before_model_call(tmp_path):
    pipe, c, _, generator, reviewer = setup_pipeline(tmp_path, [review('pass')])
    out = pipe.run(c['source_text'], SourceMeta.model_validate(c['metadata']), '9'*64, tmp_path/'out')
    path = tmp_path/'out'/f"{out['content_id']}.json"
    before = path.read_bytes()
    data = envelope(out['content_id'])
    data['items'][0].update(sentence=len(out['script']) + 1, sent=len(out['script']) + 1)
    feedback = tmp_path/'revise.jsonl'
    feedback.write_text(json.dumps(data))
    calls = generator.calls, reviewer.calls
    with pytest.raises(ValueError, match='invalid_feedback_sentence'):
        pipe.apply_feedback(path, parse_feedback(feedback, out['content_id']), out['version'])
    assert (generator.calls, reviewer.calls) == calls
    assert path.read_bytes() == before


def test_cli_mixed_global_and_sentence_feedback_revises_and_logs_conversion(tmp_path, monkeypatch):
    import shutil
    import sys
    from script_agent import cli
    pipe, c, _, generator, _ = setup_pipeline(tmp_path, [review('pass'), review('pass')], [flow_draft()])
    out = pipe.run(c['source_text'], SourceMeta.model_validate(c['metadata']), 'a'*64, tmp_path/'out')
    path = tmp_path/'out'/f"{out['content_id']}.json"
    data = envelope(out['content_id'])
    data['items'].insert(0, {'sentence': None, 'sent': None, 'scope': 'script',
                           'problem': '전체 설명 부족', 'reason': '맥락 부족', 'direction': '근거 안에서 보완'})
    feedback = tmp_path/'revise.jsonl'
    feedback.write_text(json.dumps(data))
    before = feedback.read_bytes()
    shutil.copy2(ROOT/'config.toml', tmp_path/'config.toml')
    monkeypatch.setattr(cli, 'credentials', lambda *a, **k: {})
    monkeypatch.setattr(cli, 'Pipeline', lambda *a, **k: pipe)
    monkeypatch.setattr(sys, 'argv', ['script-agent', '--root', str(tmp_path), 'revise', str(path),
                                    str(feedback), '--expected-version', out['version']])
    assert cli.main() == 0
    assert [i['sentence'] for i in generator.payloads[-1]['feedback']] == [0, 1]
    assert json.loads(path.read_text())['generator']['revision'] == 0
    assert json.loads(path.read_text())['generator']['feedback_revision'] == 1
    logs = list((path.parent/'logs').glob('*/external-*/feedback-normalizations.json'))
    assert len(logs) == 1
    assert json.loads(logs[0].read_text()) == [
        {'item': 1, 'scope': 'script', 'original_fields': {'sentence': None, 'sent': None}, 'sentence': 0}]
    assert feedback.read_bytes() == before


def test_shared_scope_prompt_is_loaded_only_for_revision(tmp_path):
    pipe, _, _, _, _ = setup_pipeline(tmp_path, [])
    instructions = (ROOT/'prompts/feedback-scope.txt').read_text().strip()
    for version in ['v6', 'v7']:
        pipe.prompt_dir = tmp_path/'prompts'/version
        assert instructions in pipe.prompt('revise')
        assert instructions not in pipe.prompt('review')
