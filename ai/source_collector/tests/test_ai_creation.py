from dataclasses import replace
from pathlib import Path
import yaml
import pytest

from source_collector.generator import SourceGenerator
from source_collector.prescreen import Deduplicator
from source_collector.writer import SourceWriter
from source_collector.judge import HeadlessJudge
from script_agent.io import read_source
from script_agent.rights import resolve_rights


@pytest.mark.parametrize('category', ['C01','C02','C03','C04','C05'])
def test_ai_story_never_exports_model_url_or_false_collection_evidence(tmp_path, monkeypatch, category):
    dedup = Deduplicator([], tmp_path/'absent')
    gen = SourceGenerator(dedup)
    monkeypatch.setattr(gen, '_request_candidates_from_llm', lambda **kw: [
        {'title':'오해를 풀어낸 하루', 'author':'익명', 'url':'https://www.instagram.com/p/Fabricated/',
         'text':'친구와 오래 이야기를 나누었습니다. ' * 40}])
    candidate, = gen.generate_for_category(category, 1, 'creation-test')
    assert candidate.ai_generated and candidate.url == candidate.attribution == candidate.source_id_ref == ''
    assert not dedup.existing_urls
    row = candidate.to_csv_row()
    assert row['원본_URL'] == row['원천목록_ID'] == '' and row['원천_명칭'] == 'AI 창작물'
    writer = SourceWriter(tmp_path/'out', tmp_path/'library')
    path = writer.save(candidate)
    raw = path.read_text()
    assert 'instagram' not in raw.lower() and '대조 완료' not in raw and '게시일: 2026' not in raw
    text, meta, _ = read_source(path)
    assert meta.source_url is None and meta.attribution == '' and meta.source_kind == 'original'
    resolved, notes, status = resolve_rights(meta)
    assert resolved.attribution == '' and status == 'metadata_supplied_not_legal_approval'
    assert not dedup.check_candidate(replace(candidate, id='another-id')).is_valid  # same title
    candidate = HeadlessJudge(mock=True).evaluate_and_ensure(candidate)
    assert candidate.ai_generated and candidate.url == ''
    assert 'AI 창작물' in writer.format_markdown(candidate)


def test_creation_marker_cannot_mask_external_provenance(tmp_path):
    gen = SourceGenerator(Deduplicator([], tmp_path/'absent'), mock=True)
    candidate, = gen.generate_for_category('C01', 1, 'test')
    writer = SourceWriter(tmp_path/'out', tmp_path/'library')
    for change in ({'url':'https://www.instagram.com/p/Fake/'}, {'attribution':'원문'}, {'source_id_ref':'SRC-021'}):
        bad = replace(candidate, **change)
        assert not Deduplicator([], tmp_path/'absent').check_candidate(bad).is_valid
        with pytest.raises(ValueError, match='invalid_ai_creation_provenance'):
            writer.save(bad)
    assert not (tmp_path/'library').exists()


def test_real_source_keeps_its_url_and_attribution(tmp_path):
    gen = SourceGenerator(Deduplicator([], tmp_path/'absent'), mock=True)
    candidate, = gen.generate_for_category('C01', 1, 'test')
    real = replace(candidate, ai_generated=False, url='https://www.instagram.com/p/Existing/',
                   source_id_ref='SRC-021', source_name='Instagram', attribution='실제 수집 출처',
                   license_kind='unknown', license_info={'kind':'unknown'})
    path = SourceWriter(tmp_path/'out', tmp_path/'library').save(real)
    _, meta, _ = read_source(path)
    assert meta.source_url == real.url and meta.attribution == real.attribution
    assert resolve_rights(meta)[2] == 'unverified'
