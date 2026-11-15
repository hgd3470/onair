"""제작진 피드백 → 옵티마이저 → 다음 생산 반영 흐름 (모델 호출은 가짜로)"""
import json
import time
from types import SimpleNamespace

from teum_factory import feedback


def make(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    v = repo / "ai/script_agent/prompts/v9"
    v.mkdir(parents=True)
    for f in ("common.txt", "generate.txt", "review.txt", "revise.txt", "analyze.txt"):
        (v / f).write_text(f"{f} 기본\n", encoding="utf-8")
    (v / "types.json").write_text("{}", encoding="utf-8")
    (repo / "ai/script_agent/prompts/feedback-scope.txt").write_text("scope", encoding="utf-8")
    (repo / "ai/script_agent/config.toml").write_text('[pipeline]\nprompt_version = "v9"\n', encoding="utf-8")
    sa = home / "script_agent"
    sa.mkdir(parents=True)
    (sa / "prompts").symlink_to(repo / "ai/script_agent/prompts")  # 지금 서버와 같은 모양
    return SimpleNamespace(repo=repo, home=home, sa_root=sa)


def add_notes(cfg, *notes):
    d = feedback.fb_dir(cfg)
    rows = [{"id": f"n{i}", "stage": st, "target": "", "text": t, "status": "new", "at": time.time()} for i, (st, t) in enumerate(notes)]
    (d / "notes.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def fake(system, payload):
    ids = [n["id"] for n in payload["new_feedback"]]
    return {"rules": [{"text": "도입 첫 문장은 질문으로 시작한다.", "from_notes": ids}],
            "notes": [{"id": i, "action": "applied", "reason": "일반화 가능"} for i in ids],
            "owner_proposal": "" if payload["auto"] else "검수 프롬프트의 R2 문단에 '숫자는 반올림 허용'을 넣는다",
            "summary": "도입 규칙 추가"}


def test_waits_for_daily_time_then_builds_team_prompt(tmp_path, monkeypatch):
    cfg = make(tmp_path)
    monkeypatch.setenv("TEUM_OPTIMIZE_AT", "04:30")
    add_notes(cfg, ("generate", "도입이 밋밋해요"))
    d = feedback.fb_dir(cfg)
    (d / "state.json").write_text(json.dumps({"last_run": time.mktime((2026, 11, 14, 4, 31, 0, 0, 0, -1))}))
    before = time.mktime((2026, 11, 15, 4, 0, 0, 0, 0, -1))
    out = feedback.step(cfg, False, call=fake, now=before)
    assert "ran" not in out and out["next_at"] == time.mktime((2026, 11, 15, 4, 30, 0, 0, 0, -1))
    # 4:30이 지나면 돈다. 생성기 작업이 남아 있으면 판은 기다린다
    out = feedback.step(cfg, False, pending_jobs=2, call=fake, now=before + 3600)
    assert out["ran"]["generate"]["rules"] == 1 and "prompt_waiting" in out
    assert feedback.active_prompt(cfg) is None
    notes = json.loads((d / "notes.json").read_text())
    assert notes[0]["status"] == "applied" and notes[0]["run"]
    # 작업이 비면 켠다. 판은 저장소 판 복사 + 규칙
    out = feedback.step(cfg, False, pending_jobs=0, call=fake, now=before + 4000)
    name = feedback.active_prompt(cfg)
    assert name and name.startswith("v9-team-") and out["prompt_activated"] == name
    common = (cfg.sa_root / "prompts" / name / "common.txt").read_text()
    assert common.startswith("common.txt 기본") and "도입 첫 문장은 질문으로" in common
    assert not (cfg.sa_root / "prompts").is_symlink() and (cfg.sa_root / "prompts/v9/generate.txt").exists()
    assert (cfg.sa_root / "prompts/feedback-scope.txt").read_text() == "scope"


def test_owner_stages_get_proposals_and_longform_gets_prefs(tmp_path):
    cfg = make(tmp_path)
    add_notes(cfg, ("review", "숫자 반올림까지 지적해요"), ("longform", "도입이 길어요"))
    (feedback.fb_dir(cfg) / "optimize_now").write_text("")
    out = feedback.step(cfg, False, call=fake)
    assert set(out["ran"]) == {"review", "longform"}
    d = feedback.fb_dir(cfg)
    review = json.loads((d / "rules/review.json").read_text())
    assert review["owner_proposal"] and not review["auto"]
    notes = {n["stage"]: n for n in json.loads((d / "notes.json").read_text())}
    assert notes["review"]["status"] == "proposed" and notes["longform"]["status"] == "applied"
    prefs = json.loads((cfg.home / "longform/prefs.json").read_text())
    assert [p["by"] for p in prefs] == ["optimizer"] and prefs[0]["active"]
    assert not (d / "optimize_now").exists() and feedback.active_prompt(cfg) is None


def test_model_error_keeps_notes_new(tmp_path):
    cfg = make(tmp_path)
    add_notes(cfg, ("generate", "x"))
    (feedback.fb_dir(cfg) / "optimize_now").write_text("")

    def boom(system, payload):
        raise RuntimeError("down")
    out = feedback.step(cfg, False, call=boom)
    assert "error" in out["ran"]["generate"]
    assert json.loads((feedback.fb_dir(cfg) / "notes.json").read_text())[0]["status"] == "new"


def test_notes_added_during_run_are_kept(tmp_path):
    cfg = make(tmp_path)
    add_notes(cfg, ("generate", "a"))
    (feedback.fb_dir(cfg) / "optimize_now").write_text("")
    d = feedback.fb_dir(cfg)

    def slow(system, payload):  # 모델을 부르는 사이에 스튜디오가 의견을 더한다
        rows = json.loads((d / "notes.json").read_text())
        rows.append({"id": "late", "stage": "generate", "text": "b", "status": "new"})
        (d / "notes.json").write_text(json.dumps(rows))
        return fake(system, payload)
    feedback.step(cfg, False, call=slow)
    st = {n["id"]: n["status"] for n in json.loads((d / "notes.json").read_text())}
    assert st == {"n0": "applied", "late": "new"}
