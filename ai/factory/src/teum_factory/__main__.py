"""python -m teum_factory {preflight | tick [--dry-run] | loop | status}

preflight  키·스튜디오·작업 폴더가 준비됐는지 (값은 찍지 않는다)
tick       한 바퀴만 돈다. --dry-run이면 무엇을 할지만 보여 주고 API를 부르지 않는다
loop       TEUM_FACTORY=1일 때 TEUM_FACTORY_INTERVAL초마다 돈다. 꺼져 있으면 기다리기만 한다
status     대본이 단계마다 몇 편인지와 사람 확인으로 넘긴 대본
"""
import argparse
import fcntl
import json
import os
import shutil
import sys
import time

from .config import Config
from .state import State
from .steps import ensure_layout, pending_inputs, pending_jobs, refine_cost_by_day, refine_cost_note, tick
from .studio import Studio, StudioError

STAGES = {1: "쓰는 중", 2: "제출 · 검수 대기", 3: "통과 · 음성 대기", 4: "음성 완료 · 듣기 대기", 5: "게시됨"}


def locked_tick(cfg, dry):
    cfg.work.mkdir(parents=True, exist_ok=True)
    with open(cfg.work / ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"skipped": "앞선 실행이 아직 돌고 있습니다"}
        report = tick(cfg, State(cfg.work / "state.json"), Studio(cfg.studio_url, cfg.token), dry)
    if not dry:
        with open(cfg.work / "log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False) + "\n")
    return report


def describe(cfg, name):
    """그 단계가 지금 무슨 모델로 도는지 한 줄"""
    from . import models
    s = cfg.slots()[name]
    if s.provider == "local":
        try:
            return f"local: {models.local_model(s.model)} ({models.local_url()})"
        except Exception:
            return f"local: 모델 서버에 붙지 못함 ({models.local_url()})"
    return f"{s.provider}: {s.model or '저장소 기본 모델'}" + ("" if s.chosen else " (설정을 비워 둔 기본값)")


def preflight(cfg):
    ensure_layout(cfg)
    ffmpeg = os.environ.get("TEUM_FFMPEG", "ffmpeg")
    here = os.path.dirname(sys.executable)  # 사이트는 teum_up.py가 이 폴더를 PATH에 넣어 띄운다
    checks = {"작업 폴더": str(cfg.home), "자동 실행(TEUM_FACTORY)": "켜짐" if cfg.enabled else "꺼짐",
              "② 대본 작성 모델": describe(cfg, "generator"), "② 자체 검토 모델": describe(cfg, "reviewer"),
              "② 생성": cfg.generator_blocker() or "준비됨",
              "③ 검수 모델": "규칙 검사만(시험용)" if cfg.rules_only else describe(cfg, "refine"),
              "③ 검수": cfg.refine_blocker() or "준비됨",
              "③ 검수 범위": cfg.refine_label(), "입력함 대기": len(pending_inputs(cfg)),
              "① 수집": (f"{cfg.collect_cmd[0]} … ({cfg.collect_interval // 3600}시간마다)" if cfg.collect_cmd
                        else "수집 명령 없음 (TEUM_COLLECT_CMD). 사람이 넣은 소재만 쓴다"),
              "② 이어 갈 생성 작업": len(pending_jobs(cfg)),
              "④ 음성 자동 합성": "켜짐 (하루 " + os.environ.get("TEUM_DAILY_CREDITS", "5000") + "자)" if os.environ.get("TEUM_AUTO_VOICE", "0") == "1"
                               else "꺼짐 (TEUM_AUTO_VOICE=1로 켠다. 꺼 두면 검수 통과 대본은 사람이 사이트에서 음성을 만든다)",
              "④ 음원 변환(ffmpeg)": "있음" if shutil.which(ffmpeg) or shutil.which(ffmpeg, path=here) else f"없음 ({ffmpeg}). 음원을 붙이지 못한다",
              "⑤ 자동 게시 제외": "; ".join(x for x in (
                  "·".join(cfg.refine_skipped(publish_only=True)) + "를 건너뛰는 설정이라 자동 검수 통과 대본 전부" if cfg.refine_skipped(publish_only=True) else "",
                  "분류 " + ",".join(sorted(cfg.publish_hold_topics)) if cfg.publish_hold_topics else "") if x) or "없음",
              "⑤ 게시": {"0": "사람이 듣고 게시", "own": "자동 생성 대본은 받아쓰기 대조 통과 시 자동 게시",
                        "all": "음성 완료 대본 전부 받아쓰기 대조 통과 시 자동 게시"}.get(cfg.auto_publish, "사람이 듣고 게시")}
    try:
        n = len(Studio(cfg.studio_url, cfg.token).data()["scripts"])
        checks["스튜디오"] = f"연결됨 ({cfg.studio_url}, 대본 {n}편)"
    except StudioError as e:
        checks["스튜디오"] = f"연결 안 됨: {e}"
    return checks


def status(cfg):
    scripts = Studio(cfg.studio_url, cfg.token).data()["scripts"]
    by = {}
    for s in scripts:
        by[s["stage"]] = by.get(s["stage"], 0) + 1
    st = State(cfg.work / "state.json").d["items"]
    return {"단계별": {STAGES.get(k, str(k)): by[k] for k in sorted(by)},
            "자동 생성 대본": sum(1 for v in st.values() if v.get("own")),
            "사람 확인으로 넘긴 대본": {k: v.get("stop") or "검수기가 사람 확인으로 판정" for k, v in st.items() if v.get("decision") == "human" or v.get("stop")},
            "입력함 대기": len(pending_inputs(cfg)),
            "검수 비용(USD)": {**refine_cost_note(cfg), "by_day": dict(sorted(refine_cost_by_day(cfg).items())[-7:])}}


def main():
    ap = argparse.ArgumentParser(prog="teum_factory", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["preflight", "tick", "loop", "status"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = Config.load()
    show = lambda x: print(json.dumps(x, ensure_ascii=False, indent=1), flush=True)  # noqa: E731
    if a.command == "preflight":
        return show(preflight(cfg))
    try:
        if a.command == "status":
            ensure_layout(cfg)
            return show(status(cfg))
        if a.command == "tick":
            return show(locked_tick(cfg, a.dry_run))
    except StudioError as e:
        sys.exit(f"스튜디오에 붙지 못했습니다: {e}")
    # 스튜디오 '자동 생산' 화면이 factory/ 아래 깃발 파일로 루프를 움직인다.
    # paused: 있으면 정해진 바퀴를 건너뛴다 / run_now: 있으면 기다리지 않고 한 바퀴 돈다(일시정지 중이어도)
    # loop.json: 루프가 살아 있는지, 언제 돌았고 다음은 언제인지 화면에 보여 줄 심장 박동
    cfg.work.mkdir(parents=True, exist_ok=True)
    paused, run_now, beat_f = cfg.work / "paused", cfg.work / "run_now", cfg.work / "loop.json"
    beat = {"pid": os.getpid(), "started": time.time(), "interval": cfg.interval, "enabled": cfg.enabled}

    def pulse(**kw):
        beat.update(kw, ts=time.time())
        tmp = beat_f.with_suffix(".tmp")
        tmp.write_text(json.dumps(beat), encoding="utf-8")
        os.replace(tmp, beat_f)

    # teum_up이 사이트와 루프를 함께 띄우므로, 사이트가 뜨기 전에 첫 바퀴를 돌면 '스튜디오에 연결할 수 없습니다'가 기록된다.
    # 첫 바퀴 전에 사이트가 응답할 때까지 최대 2분 기다린다
    import urllib.error
    import urllib.request
    for _ in range(60):
        try:
            urllib.request.urlopen(cfg.studio_url + "/admin/login", timeout=3)
            break
        except urllib.error.HTTPError:
            break  # 응답이 왔으면 떠 있다
        except OSError:
            time.sleep(2)
    forced = False
    while True:  # loop
        if cfg.enabled and (forced or not paused.exists()):
            pulse(running=True, last_start=time.time(), forced=forced)
            try:
                show(locked_tick(cfg, False))
            except Exception as e:  # 한 바퀴가 실패해도 다음 바퀴는 돈다
                show({"error": f"{type(e).__name__}: {e}"[:300]})
            pulse(running=False, last_end=time.time())
        end = time.time() + cfg.interval
        pulse(next_at=end)
        forced = False
        while time.time() < end:
            if run_now.exists():
                run_now.unlink(missing_ok=True)
                forced = True
                break
            time.sleep(3)


if __name__ == "__main__":
    sys.exit(main())
