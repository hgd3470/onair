"""소재 → 생성 → 스튜디오 제출 → 검수 → 수정 왕복을 실제 스튜디오 프로세스에 붙여 한 번에 확인한다.

모델·TTS는 부르지 않는다: 생성기는 fake_script_agent.py, 검수는 규칙 검사만, 음성 자동 합성은 끈다.
실행: PYTHONPATH=ai/factory/src python -m pytest ai/factory/tests -q   (저장소 루트에서)
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SAMPLE = REPO / "ai/refine/tests/fixtures/generator/real_readaloud.json"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def studio(tmp_path, monkeypatch):
    home, samples, port = tmp_path / "home", tmp_path / "samples", free_port()
    samples.mkdir()
    base = json.loads(SAMPLE.read_text(encoding="utf-8"))
    for n in (1, 2):
        d = {**base, "content_id": f"S2-aaaa00000000000{n}", "title": f"{base['title']} {n}"}
        (samples / f"{d['content_id']}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    env = {"TEUM_HOME": str(home), "TEUM_PASS": "test-pass", "TEUM_COOKIE_KEY": "k" * 64, "TEUM_SERVICE_TOKEN": "t" * 64,
           "TEUM_AUTO_VOICE": "0", "TEUM_REFINE_RULES_ONLY": "1", "TEUM_STUDIO_URL": f"http://127.0.0.1:{port}",
           "TEUM_FAKE_SAMPLES": str(samples), "TEUM_PYTHON": sys.executable,
           "TEUM_SCRIPT_AGENT_CMD": f"{sys.executable} {Path(__file__).parent / 'fake_script_agent.py'}"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    subprocess.run([sys.executable, str(REPO / "infra/one-server/teum_up.py"), "init"], env={**os.environ, **env},
                   check=True, capture_output=True)  # 새 서버에 처음 까는 절차 그대로
    log = open(tmp_path / "studio.log", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "teum_gate:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=REPO / "services/studio", env={**os.environ, **env}, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/admin/login", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("스튜디오가 뜨지 않음: " + (tmp_path / "studio.log").read_text(errors="replace")[-800:])
        yield home
    finally:
        proc.terminate()
        proc.wait(10)


def test_one_tick_takes_material_to_a_verdict(studio):
    from teum_factory.__main__ import locked_tick, status
    from teum_factory.config import Config
    from teum_factory.state import State
    from teum_factory.steps import ensure_layout
    from teum_factory.studio import Studio

    cfg = Config.load()
    ensure_layout(cfg)
    (cfg.sa_root / "input" / "01_story.md").write_text("# 소재 1\n", encoding="utf-8")
    (cfg.sa_root / "input" / "02_hold.md").write_text("# 소재 2 (생성기가 보류하는 경우)\n", encoding="utf-8")

    dry = locked_tick(cfg, True)
    assert dry["2_generate"]["would_generate"] == 2
    assert not list((cfg.sa_root / "output").glob("*.json")), "dry-run은 아무것도 만들지 않는다"

    report = locked_tick(cfg, False)
    assert report["2_generate"]["generated"] == 2
    assert report["2_submit"] == {"uploaded": ["s2-aaaa000000000001"], "held": ["s2-aaaa000000000002"], "errors": []}
    assert not any(r[k]["errors"] for r in report["3_review"] for k in ("refine", "revise"))

    site = Studio(cfg.studio_url, cfg.token).scripts()
    held, done = site["s2-aaaa000000000002"], site["s2-aaaa000000000001"]
    assert held["stage"] == 1 and held["script_status"] == "초안", "생성기가 보류한 대본은 제출하지 않고 사람에게 남긴다"
    item = State(cfg.work / "state.json").get("s2-aaaa000000000001")
    assert item["own"] and item["decision"] in ("pass", "human"), "한 바퀴 안에 통과하거나 사람 확인으로 넘어간다"
    if item["decision"] == "pass":
        assert done["stage"] == 3 and done["refine_status"] == "통과"
    else:
        assert "사람 확인 필요" in done["refine_memo"]
    assert done["refine_memo"].startswith("[자동 검수"), "자동 판정은 사람 판정과 구분되게 표시한다"
    assert "LLM 검사" in done["refine_memo"], "건너뛴 검사는 사유에 드러난다"

    again = locked_tick(cfg, False)
    assert again["2_generate"]["generated"] == 0 and again["2_submit"]["uploaded"] == []
    assert all(r["refine"]["reviewed"] == 0 for r in again["3_review"]), "같은 판을 두 번 검수하지 않는다"
    assert status(cfg)["자동 생성 대본"] == 2


def test_checked_audio_is_published_without_a_person(studio, tmp_path, monkeypatch):
    """TEUM_AUTO_PUBLISH=1: 음성이 붙고 받아쓰기 대조를 통과한 자동 생성 대본만 게시한다. 대조 전·대조 불일치·검사를 건너뛴 대본·사람 승인으로 남긴 분류·생성기가 공개 전 검토 대기로 둔 대본은 게시하지 않는다"""
    import sqlite3

    from teum_factory.__main__ import locked_tick
    from teum_factory.config import Config
    from teum_factory.state import State
    from teum_factory.steps import ensure_layout
    from teum_factory.studio import Studio

    monkeypatch.setenv("TEUM_AUTO_PUBLISH", "1")
    cfg = Config.load()
    ensure_layout(cfg)
    (cfg.sa_root / "input" / "01_story.md").write_text("# 소재 1\n", encoding="utf-8")
    first = locked_tick(cfg, False)
    assert first["5_publish"]["published"] == [], "음성이 없으면 게시할 것이 없다"
    cid, site = "s2-aaaa000000000001", Studio(cfg.studio_url, cfg.token)
    if State(cfg.work / "state.json").get(cid)["decision"] != "pass":
        site.update(cid, "refine_status", "통과")  # 사람이 통과시킨 경우와 같다
    assert site.scripts()[cid]["stage"] == 3

    # ④ 대역: Typecast 대신 무음 mp3를 스튜디오가 쓰는 것과 같은 명령으로 붙인다
    mp3 = tmp_path / "silence.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "1", str(mp3)], check=True)
    out = subprocess.run([sys.executable, str(REPO / "services/studio/pipeline_ops.py"), "attach", cid, str(mp3), "test-voice"],
                         capture_output=True, text=True, env=os.environ.copy())
    assert json.loads(out.stdout.strip().splitlines()[-1])["ok"], out.stdout + out.stderr
    assert site.scripts()[cid]["stage"] == 4

    def set_stt(stt):
        db = sqlite3.connect(studio / "admin_data/admin.db", timeout=10)
        d = json.loads(db.execute("select data from items where tbl='scripts' and id=?", (cid,)).fetchone()[0])
        d["stt"] = stt
        db.execute("update items set data=? where tbl='scripts' and id=?", (json.dumps(d, ensure_ascii=False), cid))
        db.commit()
        db.close()

    st = State(cfg.work / "state.json")
    if st.get(cid).get("rules_only"):
        r = locked_tick(cfg, False)["5_publish"]
        assert r["skipped_check_pass"] == {cid: "LLM 검사·안전 검사"} and r["published"] == [], "검사를 건너뛴 채 통과한 대본은 자동 게시하지 않는다"
        st = State(cfg.work / "state.json")
        st.note(cid, rules_only=False, skipped=["안전 검사"])  # LLM 검사는 했지만 안전 검사 서버(TEUM_KANANA_URL)가 없던 경우
        st.save()
        r = locked_tick(cfg, False)["5_publish"]
        assert r["skipped_check_pass"] == {cid: "안전 검사"} and r["published"] == [], "안전 검사 없이 통과한 대본도 자동 게시하지 않는다"
        st = State(cfg.work / "state.json")
        st.note(cid, skipped=[])  # 검사를 다 거쳐 통과한 경우
        st.save()
    r = locked_tick(cfg, False)["5_publish"]
    assert r["waiting_check"] == [cid] and r["published"] == [], "받아쓰기 대조 전에는 게시하지 않는다"
    set_stt({"cer": 0.2, "diffs": [], "flag": True, "ts": 1})
    r = locked_tick(cfg, False)["5_publish"]
    assert r["held"] == [cid] and r["published"] == [] and site.scripts()[cid]["stage"] == 4, "대조에서 걸린 음원은 사람에게 남긴다"
    set_stt({"cer": 0.0, "diffs": [], "flag": False, "ts": 2})
    topic = site.scripts()[cid].get("topic_code", "")
    cfg.publish_hold_topics = {topic}
    r = locked_tick(cfg, False)["5_publish"]
    assert r["topic_hold"] == [cid] and r["published"] == [], "사람 승인으로 남긴 분류는 자동 게시하지 않는다"
    cfg.publish_hold_topics = set()

    # 생성기가 공개 전 검토 대기로 표시한 대본은 루프도, 사이트의 '앱에 추가'도 게시하지 않는다 (#23 연동 조건)
    from teum_factory.studio import StudioError
    stored = next((studio / "scripts_live/json").glob("*0001.json"))
    original = stored.read_text(encoding="utf-8")
    stored.write_text(json.dumps({**json.loads(original), "release_status": "pending_processing_review"}, ensure_ascii=False), encoding="utf-8")
    assert site.scripts()[cid]["release_status"] == "pending_processing_review"
    r = locked_tick(cfg, False)["5_publish"]
    assert r["release_hold"] == {cid: "pending_processing_review"} and r["published"] == []
    with pytest.raises(StudioError, match="공개 전 검토"):
        site.add_to_app([cid])
    assert site.scripts()[cid]["stage"] == 4
    stored.write_text(original, encoding="utf-8")

    r = locked_tick(cfg, False)["5_publish"]
    assert r["published"] == [cid]
    assert site.scripts()[cid]["stage"] == 5, "게시됨"
    assert (studio / "data_root").exists() and locked_tick(cfg, False)["5_publish"]["published"] == []


def test_no_keys_means_nothing_is_called(tmp_path, monkeypatch):
    """키가 없으면 생성·검수를 시도하지 않고 이유를 알린다 (스튜디오 없이도 확인 가능한 부분)"""
    from teum_factory.config import Config
    from teum_factory.steps import ensure_layout, generate

    monkeypatch.setenv("TEUM_HOME", str(tmp_path))
    for k in ("TEUM_SCRIPT_AGENT_CMD", "TEUM_REFINE_RULES_ONLY", "OPENAI_API_KEY", "GEMINI_API_KEY", "openai_api_key", "gemini_api_key"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REFINE_ANTHROPIC_KEY_FILE", str(tmp_path / "none.key"))
    for k, v in (("TEUM_GENERATOR", "gemini"), ("TEUM_REVIEWER", "openai"), ("TEUM_REFINE", "claude")):
        monkeypatch.setenv(k, v)
    cfg = Config.load()
    ensure_layout(cfg)
    (cfg.sa_root / "input" / "a.md").write_text("# 소재\n", encoding="utf-8")
    assert not cfg.generator_ready() and not cfg.refine_ready()
    res = generate(cfg, False)
    assert res["generated"] == 0 and res["waiting"] == 1 and "GEMINI_API_KEY 없음" in res["blocked"] and "OPENAI_API_KEY 없음" in res["blocked"]


def test_refine_cost_is_summed_per_day(tmp_path, monkeypatch):
    """검수 비용은 바퀴마다 새 실행 폴더에 따로 남으므로 날짜로 합쳐 보이고, 기준을 넘으면 경고를 남긴다"""
    import time

    from teum_factory.config import Config
    from teum_factory.steps import refine_cost_by_day, refine_cost_note

    monkeypatch.setenv("TEUM_HOME", str(tmp_path))
    monkeypatch.setenv("TEUM_REFINE_DAILY_WARN_USD", "1")
    cfg = Config.load()
    today = time.strftime("%Y%m%d")
    for name, sub, usd in ((f"{today}-090000", "out_json", 0.4), (f"{today}-091000", "out_json", 0.5), (f"{today}-091000", "out_md", 0.2),
                           ("20260101-000000", "out_json", 3.0)):
        d = cfg.work / "runs" / name / sub
        d.mkdir(parents=True)
        (d / "cost.json").write_text(json.dumps({"cost_usd": usd}))
    days = refine_cost_by_day(cfg)
    assert days["2026-01-01"] == 3.0 and days[time.strftime("%Y-%m-%d")] == 1.1
    note = refine_cost_note(cfg)
    assert note["today_usd"] == 1.1 and "warning" in note
    monkeypatch.setenv("TEUM_REFINE_DAILY_WARN_USD", "2")
    assert "warning" not in refine_cost_note(Config.load())
