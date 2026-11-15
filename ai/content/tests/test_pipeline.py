"""실제 입력과 무음 러너로 제작 흐름을 검증한다."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

CONTENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONTENT / "src"))
sys.path.insert(0, str(CONTENT.parents[1] / "server"))
from teum_content.__main__ import main
from teum_content.pipeline import parse
from teum_content.tts_runner import main as runner
from teum.catalog.service import load_published

IDS = ["w1-c01-01", "w1-c06-01", "w1-c13-01", "w1-c19-01", "w1-c21-01", "w1-p01-01"]
FILES = [f for f in sorted((CONTENT.parents[1] / "content" / "scripts").glob("*.md")) if parse(f)["content_id"] in IDS]
CATS = ["C01", "C06", "C13", "C19", "C21", "C04"]


def command(work, *args):
    main([*map(str, args), "--work", str(work)])


def rows(work):
    with sqlite3.connect(work / "pipeline.sqlite") as db:
        db.row_factory = sqlite3.Row
        return {r["content_id"]: dict(r) for r in db.execute("SELECT * FROM item")}


def synth(work, tmp, name="first"):
    jobs, results = tmp / f"{name}-jobs.jsonl", tmp / f"{name}-results.jsonl"
    command(work, "tts-jobs", "--out", jobs)
    subprocess.run([sys.executable, "-m", "teum_content.tts_runner", str(jobs), str(results), "--stub"],
                   cwd=CONTENT / "src", check=True)
    command(work, "tts-ingest", results)
    return jobs, results


def test_parse_six():
    assert len(FILES) == 6
    data = [parse(f) for f in FILES]
    assert [d["content_id"] for d in data] == IDS
    assert [d["category"] for d in data] == CATS
    assert [d["target_sec"] for d in data] == [60, 60, 75, 75, 90, 270]
    assert all(len(d["lines"]) > 5 and d["error"] is None for d in data)
    assert all(d["script_version"] == "1.0.0" for d in data)
    assert data[0]["tags"] == ["첫 출근", "호칭", "오해"]
    assert data[0]["source"] is None
    assert data[1]["source"]["title"] == "Pack First Night Moving Boxes"
    assert len(data[5]["lines"]) == 38


def test_full_flow(tmp_path, capsys):
    work, root = tmp_path / "work", tmp_path / "data"
    command(work, "ingest", *FILES)
    jobs, results = synth(work, tmp_path)
    command(work, "tts-ingest", results)  # 동일 결과 재수신은 상태 유지
    assert all(r["state"] == "tts_done" for r in rows(work).values())
    assert all(r["attempts"] == 0 for r in rows(work).values())
    job = json.loads(jobs.read_text().splitlines()[0])
    assert job["text"] == "\n".join(parse(FILES[0])["lines"])
    for cid in IDS[:5]:
        command(work, "review", cid, "--approve")
    command(work, "review", IDS[5], "--reject", "--note", "음악 검토 필요")
    command(work, "publish", "--data-root", root)
    db_path = root / "outputs" / "catalog.sqlite"
    published = load_published(db_path)
    assert len(published) == 5
    for item in published:
        assert item["audio_uri"] == f"/gen/{item['content_id']}.v1.mp3"
        assert item["duration_sec"] > 0
        assert item["category"] == CATS[IDS.index(item["content_id"])]
        assert (root / "outputs/gen/audio" / Path(item["audio_uri"]).name).is_file()
        assert (root / "outputs/gen/audio" / Path(item["audio_uri"]).name).read_bytes().startswith(
            (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"ID3"))
    command(work, "publish", "--data-root", root)
    assert load_published(db_path) == published
    command(work, "ingest", *FILES[:5])
    assert all(rows(work)[cid]["state"] == "published" for cid in IDS[:5])
    command(work, "status")
    assert "published" in capsys.readouterr().out
    with sqlite3.connect(work / "pipeline.sqlite") as db:
        assert {r[0] for r in db.execute("SELECT DISTINCT step FROM log")} == {
            "ingest", "tts-jobs", "tts-ingest", "review", "publish", "status"}


def test_three_failures_and_stale_result(tmp_path):
    work = tmp_path / "work"
    command(work, "ingest", FILES[0])
    for attempt in range(1, 4):
        jobs = tmp_path / f"jobs{attempt}.jsonl"
        command(work, "tts-jobs", "--out", jobs)
        job = json.loads(jobs.read_text())
        result = dict(content_id=IDS[0], ok=False, wav=job["out_wav"], duration_sec=None, error="GPU OOM")
        path = tmp_path / f"results{attempt}.jsonl"
        path.write_text(json.dumps(result) + "\n")
        if attempt > 1:
            command(work, "tts-ingest", tmp_path / "results1.jsonl")
            assert rows(work)[IDS[0]]["state"] == "tts_pending"
        command(work, "tts-ingest", path)
        command(work, "tts-ingest", path)
        row = rows(work)[IDS[0]]
        assert row["attempts"] == attempt
        assert row["audio_version"] == attempt
    assert row["state"] == "failed"
    command(work, "tts-jobs", "--out", tmp_path / "empty.jsonl")
    assert (tmp_path / "empty.jsonl").read_text() == ""
    command(work, "ingest", FILES[0])
    assert rows(work)[IDS[0]]["state"] == "failed"


@pytest.mark.parametrize("text", ["# Missing ID\n", "# Empty\n## 1. 제작 정보\n| 관리번호 | TEST-01 |\n## 2. 낭독 본문\n```text\n\n```\n"])
def test_invalid(tmp_path, text):
    file = tmp_path / "bad.md"
    file.write_text(text)
    work = tmp_path / "work"
    command(work, "ingest", file)
    row, = rows(work).values()
    assert row["state"] == "invalid" and row["last_error"]
    assert (work / row["content_id"] / "script.json").exists()
    command(work, "tts-jobs", "--out", tmp_path / "jobs.jsonl")
    assert (tmp_path / "jobs.jsonl").read_text() == ""


def test_reject_rerun_and_new_publication(tmp_path):
    work, root = tmp_path / "work", tmp_path / "data"
    command(work, "ingest", FILES[0])
    with pytest.raises(ValueError):
        command(work, "review", IDS[0], "--approve")
    synth(work, tmp_path)
    command(work, "review", IDS[0], "--reject")
    command(work, "ingest", FILES[0])
    synth(work, tmp_path, "second")
    command(work, "review", IDS[0], "--approve")
    command(work, "publish", "--data-root", root)
    replacement = tmp_path / "changed.md"
    replacement.write_text(FILES[0].read_text().replace("첫 출근날,", "새로운 첫 출근날,"))
    command(work, "ingest", replacement)
    synth(work, tmp_path, "third")
    command(work, "review", IDS[0], "--approve")
    command(work, "publish", "--data-root", root)
    items = load_published(root / "outputs/catalog.sqlite")
    assert len(items) == 1
    assert items[0]["audio_uri"].endswith(".v3.mp3")
    assert items[0]["play_no"] == 1
    assert "새로운 첫 출근날" in items[0]["script"]


def test_missing_wav_counts_failure(tmp_path):
    work = tmp_path / "work"
    command(work, "ingest", FILES[0])
    jobs = tmp_path / "jobs.jsonl"
    command(work, "tts-jobs", "--out", jobs)
    job = json.loads(jobs.read_text())
    result = tmp_path / "result.jsonl"
    result.write_text(json.dumps(dict(content_id=IDS[0], ok=True, wav=job["out_wav"], duration_sec=999)))
    command(work, "tts-ingest", result)
    assert rows(work)[IDS[0]]["state"] == "tts_failed"


def test_runner_continues_after_bad_job(tmp_path):
    jobs, results = tmp_path / "jobs.jsonl", tmp_path / "results.jsonl"
    job = dict(content_id="good", text="테스트", out_wav=str(tmp_path / "audio.wav"))
    jobs.write_text("not json\n" + json.dumps(job) + "\n")
    runner([str(jobs), str(results), "--stub"])
    bad, good = map(json.loads, results.read_text().splitlines())
    assert bad["ok"] is False and bad["error"]
    assert good["ok"] is True and good["duration_sec"] > 0


def test_redo_after_publish(tmp_path):
    work, root = tmp_path / "work", tmp_path / "data"
    command(work, "ingest", FILES[0])
    synth(work, tmp_path)
    command(work, "review", IDS[0], "--approve", "--note", "ok")
    command(work, "publish", "--data-root", root)
    command(work, "redo", IDS[0], "--note", "문장 누락")
    item = rows(work)[IDS[0]]
    assert item["state"] == "script_ready" and item["audio_version"] == 1
    synth(work, tmp_path, "second")
    item = rows(work)[IDS[0]]
    assert item["state"] == "tts_done" and item["audio_version"] == 2 and item["wav"].endswith("audio.v2.wav")


def test_chunks():
    from teum_content.tts_runner import chunks
    lines = ["가" * 40, "나" * 40, "다" * 40, "라" * 200]
    assert chunks(lines, 0) == lines
    assert chunks(lines, 100) == ["가" * 40 + " " + "나" * 40, "다" * 40, "라" * 200]


def test_join_matches_sentence_pause():
    import numpy as np
    from teum_content.tts_runner import join
    rate = 24000
    tone = np.sin(np.arange(rate) / rate * 2 * np.pi * 220).astype(np.float32) * 0.3
    hush = np.zeros(rate * 3 // 10, dtype=np.float32)
    piece = np.concatenate([hush, tone, np.zeros(rate, dtype=np.float32), tone, hush])  # 문장 끝 쉼 1초
    out = join([piece, piece], rate)
    lengths = [len(x) / rate for x in np.split(out, np.flatnonzero(np.diff(np.abs(out) > 0)) + 1)
               if not np.abs(x).any() and len(x) > rate // 10]
    assert len(lengths) == 3 and all(abs(x - 1.0) < 0.15 for x in lengths)  # 경계 쉼도 1초 안팎


def test_license_from_source_section():
    licenses = {parse(f)["content_id"]: (parse(f)["source"] or {}).get("license") for f in FILES}
    assert licenses["w1-c21-01"] == "성경전서 개역한글판 © 대한성서공회 1961."
    assert licenses["w1-p01-01"] == "CC BY"
    assert licenses["w1-c06-01"] is None


def test_metadata_only_reingest_keeps_audio(tmp_path):
    work = tmp_path / "work"
    command(work, "ingest", FILES[0])
    synth(work, tmp_path)
    command(work, "review", IDS[0], "--approve", "--note", "ok")
    command(work, "publish", "--data-root", tmp_path / "data")
    edited = tmp_path / FILES[0].name
    edited.write_text(FILES[0].read_text(encoding="utf-8").replace("| 세부 태그 |", "| 세부 태그 | 추가,"), encoding="utf-8")
    command(work, "ingest", edited)
    row = sqlite3.connect(work / "pipeline.sqlite").execute(
        "SELECT state, audio_version FROM item WHERE content_id=?", (IDS[0],)).fetchone()
    assert row == ("published", 1)


def test_numbers_and_latin_are_flagged(tmp_path):
    script = tmp_path / "x.md"
    script.write_text(FILES[0].read_text(encoding="utf-8").replace("```text\n", "```text\n1954년 AI 이야기예요.\n", 1), encoding="utf-8")
    assert parse(script)["warnings"] == ["1행: 1954년 AI 이야기예요."]
    assert parse(FILES[0])["warnings"] == []


def test_question_mark_gets_longer_pause():
    import numpy as np
    from teum_content.tts_runner import join
    rate = 24000
    tone = np.sin(np.arange(rate) / rate * 2 * np.pi * 220).astype(np.float32) * 0.3
    out_q = join([tone, tone], rate, gap=1.0, ends=["뭘까요?", "끝."])
    out_p = join([tone, tone], rate, gap=1.0, ends=["그래요.", "끝."])
    assert len(out_q) - len(out_p) == round(0.2 * rate)


def test_republish_keeps_held_status(tmp_path):
    work, root = tmp_path / "work", tmp_path / "data"
    command(work, "ingest", FILES[0])
    synth(work, tmp_path)
    command(work, "review", IDS[0], "--approve", "--note", "ok")
    command(work, "publish", "--data-root", root)
    db = sqlite3.connect(root / "outputs/catalog.sqlite")
    with db:
        db.execute("UPDATE content SET status='held' WHERE content_id=?", (IDS[0],))
    command(work, "publish", "--data-root", root)
    assert db.execute("SELECT status FROM content WHERE content_id=?", (IDS[0],)).fetchone()[0] == "held"
