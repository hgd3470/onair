"""생성기(ai/script_agent, PR #23)와 루프가 맞물리는 네 지점과 수집 주기. 모델·사이트는 부르지 않는다."""
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from teum_factory import steps
from teum_factory.state import State


class FakeSite:
    def __init__(self):
        self.uploads, self.updates = [], []

    def upload(self, name, raw):
        self.uploads.append(name)
        return [{"ok": True, "msg": "ok"}]

    def update(self, *a):
        self.updates.append(a)

    def comment(self, *a):
        pass


def cfg_for(root, **kw):
    for d in ("input", "processing", "output"):
        (root / d).mkdir(exist_ok=True)
    return SimpleNamespace(sa_root=root, work=root, repo=root, max_generate=5, max_rounds=2, sa_cmd=["mock"],
                           sa_env=lambda: root / "absent", generator_blocker=lambda: "", refine_label=lambda: "[test]", **kw)


def write(path, **d):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": "1.0.0", "status": "ready_for_downstream_review", **d}), encoding="utf-8")


def test_outputs_in_batch_folders_are_submitted_and_revised(tmp_path):
    cfg = cfg_for(tmp_path)
    write(tmp_path / "output/_unbatched/S2-a.json", content_id="S2-a")
    write(tmp_path / "output/B1/S2-b.json", content_id="S2-b")
    write(tmp_path / "output/S2-c.json", content_id="S2-c")  # 예전 평면 출력
    state, site = State(tmp_path / "state.json"), FakeSite()
    assert sorted(steps.submit(cfg, state, site, False)["uploaded"]) == ["s2-a", "s2-b", "s2-c"]
    assert state.get("s2-a")["file"] == "_unbatched/S2-a.json" and sorted(site.uploads) == ["S2-a.json", "S2-b.json", "S2-c.json"]
    assert steps.output_path(cfg, state.get("s2-b")) == tmp_path / "output/B1/S2-b.json"
    assert steps.output_path(cfg, {"file": "S2-b.json"}) == tmp_path / "output/B1/S2-b.json"  # 이름만 적힌 예전 기록


def test_unfinished_job_resumes_without_new_input(tmp_path):
    cfg = cfg_for(tmp_path)
    write(tmp_path / "processing/job1/job.json", status="processing")
    with patch.dict(os.environ, {"TEUM_SCRIPT_AGENT_CMD": "mock"}), patch.object(steps, "write_sa_config"), \
         patch.object(steps, "run_cmd", return_value=(0, "[]", "")) as run:
        assert steps.generate(cfg, False)["resume_jobs"] == 1
    assert run.call_count == 1


def test_held_revision_is_shown_to_a_person_not_rereviewed(tmp_path):
    cfg = cfg_for(tmp_path)
    out = tmp_path / "output/S2-h.json"
    write(out, content_id="S2-h")
    (tmp_path / "feedback").mkdir()
    (tmp_path / "feedback/s2-h.jsonl").write_text("{}")
    state, site = State(tmp_path / "state.json"), FakeSite()
    state.note("s2-h", own=True, decision="revise", file="S2-h.json", refined_sha="old", uploaded_sha="old")

    def revise_to_hold(*a, **k):
        write(out, content_id="S2-h", version="1.0.1", status="hold")
        return 0, "", ""
    with patch.object(steps, "run_cmd", revise_to_hold):
        assert steps.revise(cfg, state, site, False)["human"] == ["s2-h"]
    it = state.get("s2-h")
    assert site.uploads == ["S2-h.json"], "사람이 최신 수정본을 본다"
    assert it["stop"] and it["refined_sha"] == steps.sha(out.read_bytes()), "같은 판을 자동 검수에 다시 넣지 않는다"


def test_collector_runs_once_per_interval_with_inbox(tmp_path):
    cfg = cfg_for(tmp_path, collect_cmd=["collect", "--output-dir", "{inbox}"], collect_interval=3600)
    with patch.object(steps, "run_cmd", return_value=(0, "", "")) as run:
        steps.collect(cfg, False)
        assert steps.collect(cfg, False)["next_collect_in_sec"] > 0
    assert run.call_count == 1 and run.call_args[0][0] == ["collect", "--output-dir", str(tmp_path / "input")]


def test_collect_due_daily_at(tmp_path):
    import time as _t
    last = _t.mktime((2026, 11, 14, 0, 30, 0, 0, 0, -1))
    assert steps.collect_due(tmp_path, last, 86400, at="05:00") == _t.mktime((2026, 11, 14, 5, 0, 0, 0, 0, -1))
    last = _t.mktime((2026, 11, 30, 5, 1, 0, 0, 0, -1))  # 5시 직후 수집 → 다음 날(달 넘김) 5시
    assert steps.collect_due(tmp_path, last, 86400, at="05:00") == _t.mktime((2026, 12, 1, 5, 0, 0, 0, 0, -1))
    assert steps.collect_due(tmp_path, 0, 86400, at="05:00") < _t.time()  # 기록 없으면 바로
    assert steps.collect_due(tmp_path, last, 3600, at="") == last + 3600
    (tmp_path / "collect_retry.txt").write_text("123.0")  # 배치 대기 재확인 시각이 우선
    assert steps.collect_due(tmp_path, last, 86400, at="05:00") == 123.0
