"""제작진 피드백 → 프롬프트 옵티마이저 → 다음 날 자동 생산에 반영 (11/14)

흐름
  1. 스튜디오 '피드백' 탭에서 담당자가 단계별로 의견을 남긴다 → $TEUM_HOME/feedback/notes.json (status=new)
  2. 매일 TEUM_OPTIMIZE_AT(기본 04:30)에 루프가 새 의견을 단계별로 모아 옵티마이저 모델(생성기와 같은 Gemini)에 보낸다.
     모델은 기존 규칙 + 새 의견을 합쳐 짧은 규칙 목록으로 다시 쓰고, 의견마다 반영/병합/제외와 이유를 돌려준다
     → feedback/rules/<단계>.json (이전 판은 feedback/history/)
  3. 반영 방식
     - generate(② 대본 생성): 생성기 프롬프트(저장소 prompts/<판>)를 복사한 '<판>-team-<시각>' 판을 생성기 작업 폴더에 만들고
       common.txt 끝에 규칙을 붙인다. 생성기 작업이 비어 있을 때 config.toml의 prompt_version을 이 판으로 바꾼다
       (작업 도중 프롬프트가 바뀌면 생성기가 checkpoint_config_changed로 멈추므로)
     - longform(롱폼): 롱폼 제작진 선호(longform/prefs.json)에 '프롬프트 옵티마이저' 이름으로 넣는다. 다음 제작부터 들어간다
     - collect(① 수집)·review(③ 검수)·voice(④ 음성): 프롬프트가 담당자 코드 안에 있어 자동으로 바꾸지 않는다.
       규칙과 함께 담당자에게 줄 '수정 제안'을 만들어 스튜디오에 보여 준다
  수동 실행: 스튜디오 '지금 반영'(feedback/optimize_now 깃발) — 시각과 상관없이 다음 바퀴에 돈다
"""
import fcntl
import json
import os
from contextlib import contextmanager
import shutil
import sys
import time
from pathlib import Path
from typing import Literal

STAGES = {
    "collect": {"label": "① 수집", "auto": False, "owner": "수집기(ai/issue_collector) 담당",
                "what": "뉴스·이슈를 골라 대본 소재로 넘기는 단계. 어떤 이슈를 고르고 어떤 정보를 붙이는지"},
    "generate": {"label": "② 대본 생성", "auto": True, "owner": "",
                 "what": "소재로 90초 안팎 차량용 오디오 대본을 쓰고 자체 검토·수정하는 단계. 말투, 구성, 길이, 도입, 숫자 읽기 등"},
    "review": {"label": "③ 검수", "auto": False, "owner": "검수기(ai/refine) 담당",
               "what": "대본의 사실·안전·청취 적합성을 검사하고 수정 지시를 내리는 단계. 무엇을 문제로 볼지, 얼마나 엄격할지"},
    "voice": {"label": "④ 음성", "auto": False, "owner": "음성·목소리 담당",
              "what": "Typecast로 음성을 만드는 단계. 목소리 선택, 속도, 쉼, 발음"},
    "longform": {"label": "롱폼 대담", "auto": True, "owner": "",
                 "what": "두 사람 대담형 8~9분 롱폼. 자료 조사, 사실 카드, 대본, 제목"},
}
MAX_RULES = 12


def fb_dir(cfg):
    d = cfg.home / "feedback"
    (d / "rules").mkdir(parents=True, exist_ok=True)
    (d / "history").mkdir(exist_ok=True)
    return d


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def notes_lock(d):
    """notes.json은 스튜디오(의견 추가)와 루프(상태 갱신)가 같이 쓴다. 쓰기 전에 잡는다"""
    with open(d / ".notes.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def next_daily(last, at):
    """last(epoch) 뒤에 처음 오는 매일 at(HH:MM, 서버 시각)"""
    h, m = (int(x) for x in at.split(":"))
    t = time.localtime(last)
    day = 0
    while True:  # mktime이 날짜 넘침(31일+1)을 알아서 맞춘다
        due = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + day, h, m, 0, 0, 0, -1))
        if due > last:
            return due
        day += 1


# ---- 옵티마이저 모델 호출 ----
OPT_SYSTEM = """너는 오디오 콘텐츠 제작 파이프라인의 프롬프트 옵티마이저다.
제작진(사람)이 한 단계에 남긴 피드백을 읽고, 그 단계 모델 프롬프트 끝에 붙일 '제작진 규칙' 목록을 다시 쓴다.

원칙
- 결과 rules는 기존 규칙과 새 피드백을 합친 '전체 목록'이다. 최대 {max_rules}개, 각 1~2문장, 모델이 바로 따를 수 있게 구체적으로 쓴다.
- 기존 규칙은 새 피드백과 충돌하지 않으면 유지한다. 충돌하면 더 최근 피드백을 따른다. 같은 뜻은 하나로 합친다.
- 한 편에만 해당하는 지적은 다른 편에도 통하는 형태로 일반화할 수 있을 때만 규칙으로 만든다. 아니면 skipped로 두고 이유를 쓴다.
- 기본 프롬프트의 사실성·안전·저작권·개인정보 규칙을 약하게 만드는 피드백은 반영하지 않는다(skipped, 이유 명시).
- 모호한 피드백은 가장 보수적인 해석으로 반영하거나, 해석이 불가능하면 skipped.
- 각 규칙의 from_notes에는 근거가 된 피드백 id(기존 규칙을 유지한 경우 기존 규칙의 from_notes)를 넣는다.
- notes에는 이번에 받은 새 피드백 각각에 대해 action(applied: 새 규칙으로 반영, merged: 기존 규칙에 합침, skipped: 반영 안 함)과 한국어 이유 한 문장을 쓴다.
- owner_proposal: 이 단계가 자동 반영 단계가 아니면(auto=false), 담당자가 프롬프트·설정의 어디를 어떻게 바꾸면 되는지 한국어로 구체적으로 제안한다(바꿀 문장 예시 포함). auto=true면 빈 문자열.
- summary: 이번에 무엇이 바뀌었는지 한국어 한두 문장.
"""


def optimize_stage(cfg, stage, current, new_notes, base_prompt, call=None):
    """한 단계의 규칙을 다시 쓴다. call(system, payload) → dict 를 넘기면 그걸 쓴다(테스트용)"""
    from pydantic import BaseModel

    class Rule(BaseModel):
        text: str
        from_notes: list[str]

    class NoteResult(BaseModel):
        id: str
        action: Literal["applied", "merged", "skipped"]
        reason: str

    class Optimized(BaseModel):
        rules: list[Rule]
        notes: list[NoteResult]
        owner_proposal: str
        summary: str

    meta = STAGES[stage]
    payload = {"stage": meta["label"], "stage_does": meta["what"], "auto": meta["auto"],
               "base_prompt_excerpt": base_prompt[:12000],
               "current_rules": [{"text": r["text"], "from_notes": r.get("from_notes", [])} for r in current],
               "new_feedback": [{"id": n["id"], "target": n.get("target", ""), "text": n["text"]} for n in new_notes]}
    system = OPT_SYSTEM.format(max_rules=MAX_RULES)
    if call:
        out = Optimized.model_validate(call(system, payload))
    else:
        sys.path.insert(0, str(cfg.repo / "ai" / "script_agent" / "src"))
        import tomllib
        from script_agent.providers import APIProvider
        from .steps import key_env
        settings = tomllib.loads((cfg.repo / "ai/script_agent/config.toml").read_text(encoding="utf-8"))["models"]["generator"]
        keys = {k.lower(): v for k, v in key_env(cfg).items()}
        out = APIProvider(settings, keys, 300).complete(system, payload, Optimized).value
    rules = [{"text": r.text.strip(), "from_notes": r.from_notes} for r in out.rules if r.text.strip()][:MAX_RULES]
    return {"rules": rules, "notes": {n.id: {"action": n.action, "reason": n.reason} for n in out.notes},
            "owner_proposal": out.owner_proposal.strip() if not meta["auto"] else "", "summary": out.summary.strip()}


def base_prompt(cfg, stage):
    """옵티마이저가 참고할 그 단계 기본 프롬프트(일부). 없으면 빈 문자열"""
    r = cfg.repo
    try:
        if stage == "generate":
            v = repo_prompt_version(cfg)
            return "\n\n".join((r / "ai/script_agent/prompts" / v / f).read_text(encoding="utf-8") for f in ("common.txt", "generate.txt"))
        if stage == "review":
            return (r / "ai/refine/PROMPTS.md").read_text(encoding="utf-8")
        if stage == "collect":
            return "\n\n".join(p.read_text(encoding="utf-8") for p in sorted((r / "ai/issue_collector/prompts").glob("*.txt")))
        if stage == "longform":
            src = (r / "ai/longform/longform.py").read_text(encoding="utf-8")
            return src[src.find("WRITE"):][:8000] if "WRITE" in src else ""
    except OSError:
        pass
    return ""


# ---- ② 대본 생성: 팀 규칙을 붙인 프롬프트 판 ----
def repo_prompt_version(cfg):
    import tomllib
    return tomllib.loads((cfg.repo / "ai/script_agent/config.toml").read_text(encoding="utf-8"))["pipeline"]["prompt_version"]


def team_block(rules):
    return ("\n\n## 제작진 규칙 (스튜디오 피드백을 프롬프트 옵티마이저가 정리한 것. 위의 사실·안전·형식 규칙과 충돌하면 위 규칙이 이긴다)\n"
            + "\n".join(f"- {r['text']}" for r in rules) + "\n")


def link_prompts(cfg):
    """생성기 작업 폴더의 prompts가 저장소 폴더를 통째로 가리키는 링크면, 판마다 링크를 둔 진짜 폴더로 바꾼다(팀 판을 옆에 두려고)"""
    repo_p, p = cfg.repo / "ai/script_agent/prompts", cfg.sa_root / "prompts"
    if p.is_symlink():
        tmp = cfg.sa_root / ".prompts.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        for x in repo_p.iterdir():
            (tmp / x.name).symlink_to(x)
        p.unlink()
        tmp.rename(p)
    p.mkdir(parents=True, exist_ok=True)
    for x in repo_p.iterdir():  # 저장소에 새 판이 생기면 링크를 더한다
        if not (p / x.name).exists() and not (p / x.name).is_symlink():
            (p / x.name).symlink_to(x)
    return p


def build_team_prompt(cfg, rules):
    """규칙을 붙인 새 판을 만들고 이름을 돌려준다. 규칙이 없으면 None(저장소 판 그대로)"""
    if not rules:
        return None
    base = repo_prompt_version(cfg)
    p = link_prompts(cfg)
    name = f"{base}-team-{time.strftime('%Y%m%d-%H%M%S')}"
    dst = p / name
    shutil.copytree(cfg.repo / "ai/script_agent/prompts" / base, dst)
    common = dst / "common.txt"
    common.write_text(common.read_text(encoding="utf-8").rstrip("\n") + team_block(rules), encoding="utf-8")
    write_json(dst / "team.json", {"base": base, "rules": rules, "built": time.time()})
    return name


def active_prompt(cfg):
    """config.toml에 넣을 prompt_version. 팀 판이 없거나 지워졌으면 None"""
    st = read_json(cfg.home / "feedback" / "state.json", {})
    name = st.get("active_prompt")
    if name and (cfg.sa_root / "prompts" / name / "common.txt").exists():
        return name
    return None


# ---- ⑥ 롱폼: 제작진 선호에 넣는다 ----
def apply_longform(cfg, rules):
    f = cfg.home / "longform" / "prefs.json"
    rows = [r for r in read_json(f, []) if r.get("by") != "optimizer"]
    now = time.time()
    rows += [{"id": f"opt-{i + 1}", "text": r["text"], "kind": "all", "show": "", "by": "optimizer",
              "by_name": "프롬프트 옵티마이저", "at": now, "active": True} for i, r in enumerate(rules)]
    f.parent.mkdir(parents=True, exist_ok=True)
    write_json(f, rows)


# ---- 루프 한 바퀴에서 부르는 곳 ----
def step(cfg, dry, pending_jobs=0, call=None, now=None):
    """새 의견이 있고 정해진 시각이 지났으면(또는 '지금 반영' 깃발) 옵티마이저를 돌린다. 기다리던 팀 프롬프트 판을 작업이 비면 켠다"""
    now = now or time.time()
    d = fb_dir(cfg)
    st = read_json(d / "state.json", {})
    out = {}
    notes = read_json(d / "notes.json", [])
    fresh = [n for n in notes if n.get("status") == "new"]
    flag = d / "optimize_now"
    at = os.environ.get("TEUM_OPTIMIZE_AT", "04:30")
    due = flag.exists() or now >= next_daily(st.get("last_run", 0), at)
    if fresh and due and not dry:
        flag.unlink(missing_ok=True)
        st["last_run"] = now  # 실패해도 다음 날(또는 '지금 반영')까지 다시 부르지 않는다
        write_json(d / "state.json", st)
        out["ran"] = run(cfg, notes, fresh, st, call=call)
        st = read_json(d / "state.json", st)
    elif fresh and dry:
        out["would_optimize"] = len(fresh)
    elif not fresh:
        flag.unlink(missing_ok=True)
    if fresh and not due:
        out["next_at"] = next_daily(st.get("last_run", 0), at)
    # ② 팀 판 켜기: 생성기 작업이 비어 있을 때만
    nxt = st.get("next_prompt", "__none__")
    if nxt != "__none__" and not dry:
        if pending_jobs:
            out["prompt_waiting"] = f"생성기 작업 {pending_jobs}건이 끝나면 {nxt or '저장소 판'}으로 바꿉니다"
        else:
            st["active_prompt"] = nxt
            st.pop("next_prompt", None)
            st["activated_at"] = now
            write_json(d / "state.json", st)
            out["prompt_activated"] = nxt or "저장소 판"
    # 저장소 기본 판이 올라갔으면 같은 규칙으로 팀 판을 다시 만든다
    act = st.get("active_prompt")
    if act and not dry and "next_prompt" not in st:
        try:
            base = read_json(cfg.sa_root / "prompts" / act / "team.json", {}).get("base")
            if base and base != repo_prompt_version(cfg):
                rules = read_json(d / "rules" / "generate.json", {}).get("rules", [])
                st["next_prompt"] = build_team_prompt(cfg, rules)
                write_json(d / "state.json", st)
                out["prompt_rebuilt"] = f"저장소 판이 {base}에서 바뀌어 다시 만듦"
        except Exception as e:  # noqa: BLE001
            out["error"] = f"팀 판 다시 만들기 실패: {type(e).__name__}: {e}"[:300]
    return out


def run(cfg, notes, fresh, st, call=None):
    d = fb_dir(cfg)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    by_stage = {}
    for n in fresh:
        by_stage.setdefault(n.get("stage"), []).append(n)
    result, updates = {}, {}
    for stage, items in by_stage.items():
        if stage not in STAGES:
            for n in items:
                updates[n["id"]] = {"status": "skipped", "reason": "모르는 단계"}
            continue
        rf = d / "rules" / f"{stage}.json"
        cur = read_json(rf, {})
        try:
            o = optimize_stage(cfg, stage, cur.get("rules", []), items, base_prompt(cfg, stage), call=call)
        except Exception as e:  # noqa: BLE001  모델 오류는 기록만 하고 의견은 new로 남긴다
            result[stage] = {"error": f"{type(e).__name__}: {e}"[:300]}
            continue
        if cur:
            write_json(d / "history" / f"{stage}-{stamp}.json", cur)
        auto = STAGES[stage]["auto"]
        new = {"stage": stage, "version": stamp, "updated": time.time(), "rules": o["rules"], "summary": o["summary"],
               "owner_proposal": o["owner_proposal"], "auto": auto, "from_run": stamp}
        write_json(rf, new)
        for n in items:
            r = o["notes"].get(n["id"], {"action": "applied", "reason": ""})
            status = "skipped" if r["action"] == "skipped" else ("applied" if auto else "proposed")
            updates[n["id"]] = {"status": status, "action": r["action"], "reason": r["reason"], "run": stamp}
        if stage == "generate":
            st["next_prompt"] = build_team_prompt(cfg, o["rules"])  # None이면 저장소 판으로 돌아간다
        elif stage == "longform":
            apply_longform(cfg, o["rules"])
        result[stage] = {"rules": len(o["rules"]), "summary": o["summary"]}
    with notes_lock(d):  # 모델을 부르는 동안 새로 들어온 의견을 덮어쓰지 않게 다시 읽고 고친다
        cur_notes = read_json(d / "notes.json", [])
        for n in cur_notes:
            if n.get("id") in updates and n.get("status") == "new":
                n.update(updates[n["id"]])
        write_json(d / "notes.json", cur_notes)
    st["last_result"] = {"at": time.time(), "run": stamp, "stages": result}
    write_json(d / "state.json", st)
    return result
