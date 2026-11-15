"""Unit and integration tests for the Source Collection Harness."""

import csv
import json
from pathlib import Path
import pytest

from source_collector.cli import main as cli_main
from source_collector.config import CATEGORIES, CSV_FIELDNAMES, LICENSES
from source_collector.generator import SourceGenerator
from source_collector.harness import CollectionHarness
from source_collector.judge import HeadlessJudge
from source_collector.models import CandidateSource
from source_collector.prescreen import Deduplicator
from source_collector.writer import SourceWriter



@pytest.fixture(autouse=True)
def local_reference_data(tmp_path, monkeypatch):
    """Keep regression tests independent of private collection ledgers."""
    import source_collector.prescreen as prescreen
    import source_collector.cli as cli
    ledger = tmp_path / "ledger.csv"
    with ledger.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow({"소스ID": "fixture-01", "주제_제목": "시험 원장 제목",
                         "원본_URL": "https://www.instagram.com/p/DFixture01/",
                         "카테고리_코드": "C01"})
    demo = tmp_path / "demo.txt"
    demo.write_text("주제: 시험 데모 제목\n", encoding="utf-8")
    monkeypatch.setattr(prescreen, "DEFAULT_CSV_KO", ledger)
    monkeypatch.setattr(prescreen, "SOURCE_COLLECTOR_ROOT", tmp_path)
    monkeypatch.setattr(prescreen, "DEMO_TXT", demo)
    monkeypatch.setattr(cli, "DEFAULT_CSV_KO", ledger)
    monkeypatch.setattr(cli, "SOURCE_COLLECTOR_ROOT", tmp_path)


def test_deduplicator_detects_csv_and_demo_duplicates():
    dedup = Deduplicator()
    stats = dedup.stats()
    assert stats["csv_ids"] == 1
    assert stats["csv_titles"] == 1
    assert stats["demo_titles"] == 1

    # 1. Existing demo title
    res_demo = dedup.check_title("시험 데모 제목")
    assert not res_demo.is_valid
    assert res_demo.duplicate_type == "demo_title_duplicate"

    # 2. Existing CSV title
    res_csv = dedup.check_title("시험 원장 제목")
    assert not res_csv.is_valid
    assert res_csv.duplicate_type == "csv_title_duplicate"

    # 3. Brand new title
    res_new = dedup.check_title("전혀 존재하지 않는 새로운 오디오 대본 테스트 제목 9999")
    assert res_new.is_valid


def test_deduplicator_instagram_url_rules():
    dedup = Deduplicator()

    # Invalid URL for C01
    res_bad = dedup.check_url("https://example.com/not-instagram", "C01")
    assert not res_bad.is_valid
    assert res_bad.duplicate_type == "invalid_instagram_url"

    # Existing URL for C01
    res_dup = dedup.check_url("https://www.instagram.com/p/DFixture01/", "C01")
    assert not res_dup.is_valid
    assert res_dup.duplicate_type == "instagram_url_duplicate"

    # Unique valid Instagram URL for C01
    res_ok = dedup.check_url("https://www.instagram.com/p/DUniqueTestCode999/", "C01")
    assert res_ok.is_valid


def test_judge_auto_reelevation_in_mock_mode():
    judge = HeadlessJudge(min_score=9, mock=True)
    short_cand = CandidateSource(
        id="261109-9999-C01-01",
        category="C01",
        title="짧은 초안 사연",
        url="https://www.instagram.com/p/DShortTest01/",
        author="익명",
        source_id_ref="SRC-021",
        source_name="인스타그램 (창작·각색 실원문)",
        production_type="창작·각색형",
        license_kind="인스타그램 공개 게시글 (창작·각색형)",
        license_info=LICENSES["instagram"],
        attribution="test",
        summary="짧은 요약",
        text="너무 짧은 본문입니다.",
    )
    # Initial evaluate should fail (<9)
    sc0, _ = judge.evaluate(short_cand)
    assert sc0 < 9

    # evaluate_and_ensure should elevate it to >= 9
    elevated = judge.evaluate_and_ensure(short_cand)
    assert elevated.score >= 9
    assert len(elevated.text) >= 650


def test_writer_and_validator(tmp_path: Path):
    writer = SourceWriter(target_dir=tmp_path / "out", source_library_root=tmp_path / "lib")
    cand = CandidateSource(
        id="261109-9999-C13-01",
        category="C13",
        title="제임스 웹 우주망원경의 적외선 냉각 방패 메커니즘",
        url="https://www.nasa.gov/missions/webb-sunshield-test",
        author="NASA",
        source_id_ref="SRC-005",
        source_name="NASA Image and Video Library",
        production_type="사실해설형",
        license_kind=LICENSES["nasa"]["kind"],
        license_info=LICENSES["nasa"],
        attribution="자료 출처: NASA",
        summary="제임스 웹 우주망원경의 5중 태양방패 원리.",
        text=(
            "지구에서 150만 킬로미터 떨어진 라그랑주 L2 지점, 제임스 웹 우주망원경은 어떻게 영하 223도의 극저온을 유지할까요? "
            "비결은 테니스장 크기의 5중 캡톤(Kapton) 태양방패에 있습니다. "
            "두께가 0.05밀리미터에 불과한 각 막 사이를 진공 상태로 띄워 열전도를 차단하고, "
            "알루미늄과 도핑 실리콘 코팅으로 태양열의 99.999%를 우주 공간으로 반사시킵니다. "
            "그 결과 태양을 향한 1번 막은 영상 85도까지 달궈지지만, 반대편 5번 막 뒤쪽의 관측 장비는 영하 233도 이하로 유지됩니다. "
            "보이지 않는 미세한 간격과 분자 코팅의 정밀함이 135억 년 전 초기 우주의 희미한 적외선 빛을 포착하는 원동력입니다."
        ),
        score=9,
        eval_reason="구체적 수치와 메커니즘 우수",
        collected_at="2026-11-09T10:40:00+09:00",
        batch_id="261109-9999",
    )
    md_path = writer.save(cand)
    assert md_path.exists()
    is_valid, info = writer.validate_file(md_path)
    assert is_valid, f"Validation failed: {info}"
    assert info["production_type"] == "사실해설형"


def test_full_harness_sandbox_mock(tmp_path: Path):
    out_dir = tmp_path / "sandbox_good"
    csv_path = tmp_path / "test_master.csv"
    lib_dir = tmp_path / "source_library"

    harness = CollectionHarness(
        mode="sandbox",
        categories=["C01", "C06"],
        count_per_category=2,
        batch_id="261109-MOCK",
        output_dir=out_dir,
        csv_paths=[csv_path],
        source_library_root=lib_dir,
        mock=True,
    )
    summary = harness.run()
    assert summary["total_candidates"] == 4
    assert summary["total_accepted"] == 4
    assert summary["validation_failures"] == 0
    assert len(list((lib_dir / "261109-MOCK").glob("261109-MOCK-C*.md"))) == 4
    assert not list(out_dir.glob("*.md"))

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4
    assert list(rows[0].keys()) == CSV_FIELDNAMES

    log_file = Path(summary["collection_log"])
    assert log_file.exists()
    assert "261109-MOCK-C01-01" in log_file.read_text(encoding="utf-8")


def test_cli_status_and_prescreen(capsys):
    rc = cli_main(["status"])
    assert rc == 0
    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert data["total_registered_sources"] == 1

    rc2 = cli_main(["prescreen", "--title", "완전히 새로운 CLI 테스트 제목 12345", "--category", "C01"])
    assert rc2 == 0
