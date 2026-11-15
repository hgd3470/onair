"""한 번 도는 일(tick)을 단계로 나눈다. 번호는 팀 장표의 콘텐츠 파이프라인 단계와 같다.

① collect   소재가 생성기 입력함(script_agent/input)에 들어온다. 수집기가 있으면 여기서 부른다
② generate  script-agent run: 소재 → 대본 JSON
   submit    새 대본 JSON을 스튜디오에 올리고 '제출'로 넘긴다 (생성기가 보류한 대본은 올리기만 하고 사람에게)
③ refine    스튜디오의 검수 대기 대본을 받아 teum_refine으로 검사하고 판정을 스튜디오에 올린다
   revise    '수정 필요'가 나온 자동 생성 대본은 script-agent revise로 고쳐 같은 파일 이름으로 다시 올린다
④⑤는 스튜디오가 한다: 통과한 대본에 목소리를 붙여 합성하고, 사람이 듣고 게시한다
"""
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from .studio import StudioError

SA_DIRS = ("input", "processing", "output", "archive", "failed")
SA_LINKS = ("config.toml", "prompts", "schema")
SA_CONFIG_MARK = "# 자동 생성"


def sha(b):
    return hashlib.sha256(b).hexdigest()[:16]


def ensure_layout(cfg):
    for d in SA_DIRS:
        (cfg.sa_root / d).mkdir(parents=True, exist_ok=True)
    for name in SA_LINKS:  # 프롬프트·설정은 저장소 것을 그대로 본다. 실제 파일을 두면 그쪽을 쓴다
        link = cfg.sa_root / name
        if not link.exists() and not link.is_symlink():
            link.symlink_to(cfg.repo / "ai/script_agent" / name)
    (cfg.work / "runs").mkdir(parents=True, exist_ok=True)
    (cfg.work / "feedback").mkdir(parents=True, exist_ok=True)


def write_sa_config(cfg):
    """teum.env에서 고른 모델을 생성기 설정에 반영한다. 사람이 직접 둔 config.toml은 건드리지 않는다"""
    f = cfg.sa_root / "config.toml"
    ours = f.is_symlink() or not f.exists() or f.read_text(encoding="utf-8").startswith(SA_CONFIG_MARK)
    if not ours:
        return
    text = cfg.sa_config()
    if text is None:
        if not f.is_symlink():
            f.unlink(missing_ok=True)
            f.symlink_to(cfg.repo / "ai/script_agent/config.toml")
    elif f.is_symlink() or not f.exists() or f.read_text(encoding="utf-8") != text:
        f.unlink(missing_ok=True)
        f.write_text(text, encoding="utf-8")


def fit_sources(cfg, files):
    """로컬 모델 창에 들어가게 긴 소재의 뒤를 줄인다. 원본은 factory/source_originals에 남긴다"""
    limit, cut = cfg.local_source_chars(), []
    for f in files:
        raw = f.read_text(encoding="utf-8-sig")
        head, body = "", raw
        if raw.startswith("---\n") and "\n---\n" in raw:
            a, b = raw.split("\n---\n", 1)
            head, body = a + "\n---\n", b
        if len(body) <= limit:
            continue
        keep = body[:limit - 40]
        keep = keep[:max(keep.rfind("\n\n"), keep.rfind(". "), keep.rfind("다.") + 1, limit // 2)].rstrip()
        (cfg.work / "source_originals").mkdir(parents=True, exist_ok=True)
        (cfg.work / "source_originals" / f.name).write_text(raw, encoding="utf-8")
        f.write_text(head + keep + "\n", encoding="utf-8")
        cut.append(f.name)
    return cut


# 단계별 시간 제한(초). 생성기·수집기가 Gemini 배치로 돌면 결과가 몇 시간 뒤에 올 수 있어 넉넉히 둔다.
# 시간 안에 못 끝내도 생성기는 다음 바퀴에 같은 배치를 이어 받고, 수집기는 같은 날짜로 다시 불러 이어 간다
def limit(name, default):
    return int(os.environ.get(f"TEUM_{name}_TIMEOUT", default))


def run_cmd(cmd, timeout, env=None, cwd=None):
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})}, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


def sa_env(cfg):
    src = str(cfg.repo / "ai/script_agent/src") + os.pathsep + str(cfg.repo / "ai/factory/src")
    return {"PYTHONPATH": src + os.pathsep + os.environ.get("PYTHONPATH", "")}


def key_env(cfg):
    """script_agent.env의 값이 있는 줄을 환경변수로. 값은 기록에 남기지 않는다"""
    f, env = cfg.sa_env(), {}
    if not f.exists():
        return env
    for line in f.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip().strip("'\"")
        if not sep or not name or name.startswith("#") or not value:
            continue
        env[name] = value
        if name.isidentifier() and name.islower():
            env.setdefault(name.upper(), value)  # 대문자 이름만 읽는 프로그램도 있어 같이 넘긴다
    return env


def sa_args(cfg):
    args = ["--root", str(cfg.sa_root)]
    if cfg.sa_env().exists():
        args += ["--env-file", str(cfg.sa_env())]
    return args


def pending_inputs(cfg):
    return sorted(p for p in (cfg.sa_root / "input").iterdir() if p.suffix.lower() in (".md", ".txt"))


def pending_jobs(cfg):
    """생성기가 받아 놓고 끝내지 못한 작업(processing/*/job.json). 배치 제출 뒤 끊긴 작업도 여기 남는다"""
    d = cfg.sa_root / "processing"
    return sorted(d.glob("*/job.json")) if d.exists() else []


def output_files(cfg):
    """생성기 출력 대본. output/ 바로 아래(예전 방식)와 output/<배치>/, output/_unbatched/ 아래를 다 본다.
    같은 대본이 두 곳에 있으면 나중에 쓴 파일을 쓴다"""
    root, latest = cfg.sa_root / "output", {}
    for f in root.rglob("S2-*.json"):
        key = f.name.lower()
        if key not in latest or f.stat().st_mtime > latest[key].stat().st_mtime:
            latest[key] = f
    return sorted(latest.values(), key=lambda f: str(f.relative_to(root)))


def output_path(cfg, it):
    """state에 적힌 출력 파일(output 기준 상대 경로). 예전 기록처럼 이름만 있으면 하위 폴더에서 찾는다"""
    f = cfg.sa_root / "output" / it["file"]
    if f.exists() or "/" in it["file"]:
        return f
    return next((g for g in output_files(cfg) if g.name == it["file"]), f)


# ---- ① ----
def collect_due(work, last, interval, at=None):
    """다음 수집 시각(epoch). 배치 대기로 다시 볼 시각(collect_retry.txt)이 있으면 그것.
    TEUM_COLLECT_AT(HH:MM, 서버 시각)이 있으면 마지막 수집 뒤 처음 오는 그 시각, 없으면 마지막 수집 + TEUM_COLLECT_INTERVAL"""
    try:
        return float((work / "collect_retry.txt").read_text().strip())
    except (OSError, ValueError):
        pass
    at = os.environ.get("TEUM_COLLECT_AT", "") if at is None else at
    if not at:
        return last + interval
    h, m = (int(x) for x in at.split(":"))
    t = time.localtime(last)
    day = 0
    while True:  # mktime이 날짜 넘침(31일+1)을 알아서 맞춘다
        due = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + day, h, m, 0, 0, 0, -1))
        if due > last:
            return due
        day += 1


def collect(cfg, dry):
    """TEUM_COLLECT_CMD를 하루 한 번(TEUM_COLLECT_AT 시각, 없으면 TEUM_COLLECT_INTERVAL마다) 부른다. 명령 안의 {inbox}는 입력함 경로로 바뀌고, 같은 경로가 TEUM_INBOX로도 간다"""
    inbox = cfg.sa_root / "input"
    if not cfg.collect_cmd:
        return {"inbox": len(pending_inputs(cfg))}
    stamp = cfg.work / "collect_last.txt"
    try:
        last = float(stamp.read_text().strip())
    except (OSError, ValueError):
        last = 0
    due = collect_due(cfg.work, last, cfg.collect_interval)
    if time.time() < due:
        return {"inbox": len(pending_inputs(cfg)), "next_collect_in_sec": int(due - time.time())}
    cmd = [str(c).replace("{inbox}", str(inbox)) for c in cfg.collect_cmd]
    if cmd[0] in ("python", "python3"):  # 루프와 같은 파이썬으로 부른다(서버의 기본 python이 다른 버전일 수 있다)
        cmd[0] = os.environ.get("TEUM_PYTHON", sys.executable)
    if dry:
        return {"inbox": len(pending_inputs(cfg)), "would_run": cmd[0]}
    stamp.write_text(str(time.time()))  # 실패해도 다음 주기까지 다시 부르지 않는다(수집 비용이 계속 나가지 않게)
    (cfg.work / "collect_retry.txt").unlink(missing_ok=True)
    # 수집기는 자기 폴더의 .env나 환경변수에서 키를 읽는다. 생성기와 같은 키 파일(script_agent.env)의 값을 환경변수로 넘겨
    # 키를 한 곳에만 두게 한다
    try:
        rc, out, err = run_cmd(cmd, limit("COLLECT", 10800), env={**key_env(cfg), "TEUM_INBOX": str(inbox), **sa_env(cfg)}, cwd=cfg.repo)
    except subprocess.TimeoutExpired:
        rc, out, err = None, "", "시간 제한 안에 끝나지 않음"
    if rc is None or (rc == 2 and "batch_pending" in (out or "")):
        # 배치 결과를 기다리는 중이다. 하루를 기다리지 않고 TEUM_COLLECT_RETRY초 뒤에 같은 날짜로 다시 불러 이어 받는다
        retry = int(os.environ.get("TEUM_COLLECT_RETRY", "1800"))
        (cfg.work / "collect_retry.txt").write_text(str(time.time() + retry))
        return {"inbox": len(pending_inputs(cfg)), "collector": "batch_pending", "retry_in_sec": retry}
    return {"inbox": len(pending_inputs(cfg)), "collector_exit": rc, **({"error": (err or out)[-300:]} if rc else {})}


# ---- ② ----
def generate(cfg, dry):
    resume = len(pending_jobs(cfg))  # 끊긴 작업이 있으면 새 입력이 없어도 생성기를 부른다. 생성기 run이 기존 작업(원격 배치 ID 포함)을 먼저 이어 간다
    n = min(len(pending_inputs(cfg)) + resume, cfg.max_generate)
    if not n:
        return {"generated": 0}
    why = cfg.generator_blocker()
    if why:
        return {"generated": 0, "waiting": n, "blocked": "생성 멈춤: " + why}
    if dry:
        return {"generated": 0, "would_generate": n, **({"resume_jobs": resume} if resume else {})}
    write_sa_config(cfg)
    shortened = []
    if not os.environ.get("TEUM_SCRIPT_AGENT_CMD") and "local" in (cfg.slots()["generator"].provider, cfg.slots()["reviewer"].provider):
        shortened = fit_sources(cfg, pending_inputs(cfg)[:n])
    rc, out, err = run_cmd([*cfg.sa_cmd, *sa_args(cfg), "run", "--limit", n], limit("GENERATE", 21600), env={**key_env(cfg), **sa_env(cfg)})
    try:
        results = json.loads(out.strip().splitlines()[-1])
    except Exception:
        results = []
    res = {"generated": sum(1 for r in results if r.get("status") != "failed"),
           "failed": [r.get("filename") or r.get("job_id") for r in results if r.get("status") == "failed"]}
    if shortened:
        res["shortened_sources"] = shortened
    if resume:
        res["resume_jobs"] = resume
    if rc and not results:
        res["error"] = (err or out)[-300:]
    if any(r.get("http_status") in (401, 403, 429) for r in results):
        res["blocked"] = "생성기 API가 인증·한도 오류를 냈습니다. 키와 한도를 확인할 때까지 생성은 멈춥니다"
    return res


def submit(cfg, state, studio, dry):
    """생성기 출력 중 아직 안 올린 판을 스튜디오에 올린다"""
    res = {"uploaded": [], "held": [], "errors": []}
    for f in output_files(cfg):
        rel = str(f.relative_to(cfg.sa_root / "output"))
        raw = f.read_bytes()
        try:
            d = json.loads(raw)
            cid = str(d["content_id"]).lower()
        except Exception:
            res["errors"].append(f"{f.name}: JSON을 읽을 수 없음")
            continue
        it = state.get(cid) or {}
        if it.get("uploaded_sha") == sha(raw) or it.get("stop"):
            continue
        first = not it.get("uploaded_sha")
        if dry:
            res["uploaded"].append(cid)
            continue
        out = studio.upload(f.name, raw)
        bad = [r["msg"] for r in out if not r.get("ok")]
        if bad or not out:
            res["errors"].append(f"{cid}: {'; '.join(bad) or '응답 없음'}"[:200])
            state.note(cid, own=True, file=rel, stop="스튜디오가 받지 않음: " + ("; ".join(bad) or "응답 없음")[:150])
            continue
        state.note(cid, own=True, file=rel, uploaded_sha=sha(raw), version=d.get("version"))
        if d.get("status") == "hold":
            if first:
                studio.comment(cid, "[자동] 생성기가 자체 검토에서 보류한 대본입니다. 사람이 보고 제출하거나 제외해 주세요.")
            state.note(cid, stop="생성기 보류(hold)")
            res["held"].append(cid)
        else:
            if first:
                studio.update(cid, "script_status", "제출")
            res["uploaded"].append(cid)
    return res


# ---- ③ ----
def refine(cfg, state, studio, dry):
    res = {"reviewed": 0, "pass": [], "revise": [], "human": [], "errors": []}
    kit = zipfile.ZipFile(io.BytesIO(studio.review_kit()))
    rows = list(csv.DictReader(io.StringIO(kit.read("검수대상.csv").decode("utf-8-sig"))))
    originals = {Path(n).stem: kit.read(n) for n in kit.namelist() if n.startswith("json/") and n.endswith(".json")}
    todo = []
    for r in rows:
        cid = r["id"]
        it = state.get(cid) or {}
        key = sha(originals[cid]) if cid in originals else sha((r["title"] + "\n" + r["body"]).encode())
        if it.get("refined_sha") == key:
            continue  # 이 판은 이미 검사했다 (사람 확인으로 넘긴 대본을 매번 다시 검사하지 않는다)
        if cfg.refine_scope == "own" and not it.get("own"):
            continue
        todo.append((cid, key, r))
    res["waiting"] = len(todo)
    todo = todo[:cfg.max_refine]
    if not todo:
        return res
    why = cfg.refine_blocker()
    if why:
        res["blocked"] = "검수 멈춤: " + why
        return res
    if dry:
        res["would_review"] = [c for c, _, _ in todo]
        return res
    run = cfg.work / "runs" / time.strftime("%Y%m%d-%H%M%S")
    (run / "in_json").mkdir(parents=True)
    md_rows = []
    for cid, key, r in todo:
        if cid in originals:
            d = json.loads(originals[cid])
            it = state.get(cid) or {}
            if it.get("own") and isinstance(d.get("generator"), dict):
                d["generator"].setdefault("feedback_revision", it.get("rounds", 0))  # 생성기가 안 적는 판이면 왕복 횟수를 사본에만 적는다
            (run / "in_json" / f"{cid}.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            md_rows.append(r)
    env = {"PYTHONPATH": os.pathsep.join([str(cfg.repo / "ai/refine/src"), str(cfg.repo / "ai/factory/src"), os.environ.get("PYTHONPATH", "")])}
    results = []
    if any((run / "in_json").iterdir()):
        rc, out, err = run_cmd([*cfg.refine_cmd, "run", run / "in_json", "--out", run / "out_json", *cfg.refine_args()], 3600, env=env)
        if rc:
            res["errors"].append("검수기(JSON): " + (err or out)[-300:])
        results += [json.loads(p.read_text(encoding="utf-8")) for p in sorted((run / "out_json").glob("*.refine.json"))]
    if md_rows:  # 생성기 JSON 원본이 없는 대본(사람이 md로 올린 것)은 사이트 표의 본문으로 검사한다
        (run / "kit_md").mkdir()
        (run / "kit_md" / "원천목록.csv").write_bytes(kit.read("원천목록.csv"))
        with (run / "kit_md" / "검수대상.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(md_rows[0].keys()))
            w.writeheader()
            w.writerows(md_rows)
        rc, out, err = run_cmd([*cfg.refine_cmd, "run", "--site-kit", run / "kit_md", "--out", run / "out_md", *cfg.refine_args()], 3600, env=env)
        if rc:
            res["errors"].append("검수기(md): " + (err or out)[-300:])
        results += [json.loads(p.read_text(encoding="utf-8")) for p in sorted((run / "out_md").glob("*.refine.json"))]
    if not results:
        return res
    sys.path.insert(0, str(cfg.repo / "ai/refine/src"))
    from teum_refine.site import export_results  # 판정 → 사이트 표(통과 / 수정 필요 / 사람 확인) 변환은 검수기 코드를 그대로 쓴다
    export_results(results, run)
    verdicts = list(csv.DictReader((run / "site_result.csv").open(encoding="utf-8-sig")))
    feedback = {}
    fb = run / "out_json" / "revise.jsonl"
    if fb.exists():
        for line in fb.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                feedback[str(row["script_id"]).lower()] = line
    label = cfg.refine_label()
    keys = {cid: key for cid, key, _ in todo}
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["id", "판정", "사유"])
    w.writeheader()
    human = []
    for v in verdicts:
        cid = v["id"].lower()
        if cid not in keys:
            continue
        if v["판정"]:
            w.writerow({"id": cid, "판정": v["판정"], "사유": (label + " " + v["사유"]).strip()})
        else:
            human.append((cid, (label + " " + v["사유"]).strip()))
    applied = studio.upload(f"auto_review_{run.name}.csv", ("﻿" + buf.getvalue()).encode()) if buf.getvalue().count("\n") > 1 else []
    ok_ids = {r["msg"].split(":")[0] for r in applied if r.get("ok")}
    res["errors"] += [r["msg"][:160] for r in applied if not r.get("ok")]
    for v in verdicts:
        cid = v["id"].lower()
        if cid not in keys:
            continue
        res["reviewed"] += 1
        if v["판정"] and cid not in ok_ids:
            continue  # 사이트가 받지 않은 판정은 다음에 다시 본다
        if v["판정"] == "통과":
            state.note(cid, refined_sha=keys[cid], decision="pass", rules_only=cfg.rules_only, skipped=cfg.refine_skipped(publish_only=True))
            res["pass"].append(cid)
        elif v["판정"] == "수정 필요":
            if cid in feedback:
                (cfg.work / "feedback" / f"{cid}.jsonl").write_text(feedback[cid] + "\n", encoding="utf-8")
            state.note(cid, refined_sha=keys[cid], decision="revise", revised_for=None)
            res["revise"].append(cid)
    for cid, memo in human:
        try:
            studio.update(cid, "refine_memo", memo[:1000])
        except StudioError as e:
            res["errors"].append(str(e)[:160])
            continue
        state.note(cid, refined_sha=keys[cid], decision="human")
        res["human"].append(cid)
    return res


def to_human(cfg, state, studio, cid, why):
    studio.update(cid, "refine_memo", f"{cfg.refine_label()} 사람 확인 필요: {why}"[:1000])
    state.note(cid, decision="human", stop=why)


def revise(cfg, state, studio, dry):
    """'수정 필요'가 난 자동 생성 대본을 생성기에 돌려보낸다. 사람이 올린 대본은 그 사람이 고친다(지금 방식 그대로)"""
    res = {"revised": [], "human": [], "errors": []}
    for cid, it in list(state.d["items"].items()):
        if not (it.get("own") and it.get("decision") == "revise") or it.get("revised_for") == it.get("refined_sha"):
            continue
        out = output_path(cfg, it)
        fb = cfg.work / "feedback" / f"{cid}.jsonl"
        if dry:
            res["revised"].append(cid)
            continue
        if it.get("rounds", 0) >= cfg.max_rounds:
            to_human(cfg, state, studio, cid, f"자동 수정 {cfg.max_rounds}회를 다 썼습니다")
            res["human"].append(cid)
            continue
        if not out.exists() or not fb.exists():
            to_human(cfg, state, studio, cid, "자동으로 고칠 수 있는 지적이 없습니다" if out.exists() else "생성기 출력 파일이 없습니다")
            res["human"].append(cid)
            continue
        version = json.loads(out.read_text(encoding="utf-8")).get("version")
        rc, so, se = run_cmd([*cfg.sa_cmd, *sa_args(cfg), "revise", out, fb, "--expected-version", version], limit("REVISE", 10800), env={**key_env(cfg), **sa_env(cfg)})
        if rc:
            try:
                code = json.loads(se.strip().splitlines()[-1]).get("code") or "unknown"
            except Exception:
                code = (se or so).strip()[-120:]
            if any(k in str(code) for k in ("revision_budget_exhausted", "feedback_requires_human_review", "invalid_feedback", "feedback_")):
                to_human(cfg, state, studio, cid, f"생성기가 자동 수정을 받지 않았습니다 ({code})")
                res["human"].append(cid)
            else:  # 일시 오류일 수 있다. 두 번까지만 다시 해 본다
                tries = it.get("revise_fail", 0) + 1
                state.note(cid, revise_fail=tries)
                res["errors"].append(f"{cid}: revise 실패 ({code})")
                if tries >= 2:
                    to_human(cfg, state, studio, cid, f"자동 수정이 두 번 실패했습니다 ({code})")
                    res["human"].append(cid)
            continue
        raw = out.read_bytes()
        d = json.loads(raw)
        state.note(cid, rounds=it.get("rounds", 0) + 1, revised_for=it.get("refined_sha"), version=d.get("version"), revise_fail=0)
        up = studio.upload(out.name, raw)  # 같은 파일 이름 → 사이트가 '수정본 재제출 · 검수 대기'로 넘긴다
        bad = [r["msg"] for r in up if not r.get("ok")]
        if bad or not up:
            res["errors"].append(f"{cid}: {'; '.join(bad) or '응답 없음'}"[:200])
            continue
        if d.get("status") == "hold":
            # 사람이 최신 수정본을 보도록 올리되, 이 판은 자동 검수에 다시 넣지 않는다(검수 통과가 없으니 음성 합성도 돌지 않는다)
            state.note(cid, uploaded_sha=sha(raw), refined_sha=sha(raw))
            to_human(cfg, state, studio, cid, "자동 수정본을 생성기가 자체 검토에서 보류했습니다. 사이트에 올린 판이 최신 수정본입니다")
            res["human"].append(cid)
            continue
        state.note(cid, uploaded_sha=sha(raw), decision="resubmitted")
        res["revised"].append(cid)
    return res


def unchecked_checks(it):
    """자동 검수로 통과한 대본이 건너뛴 검사 이름(문자열). 다 거쳤거나 사람이 통과시킨 대본이면 빈 문자열"""
    skipped = list(it.get("skipped") or [])
    if it.get("rules_only") and "LLM 검사" not in skipped:
        skipped.insert(0, "LLM 검사")
    if not skipped and it.get("decision") == "pass" and "skipped" not in it:
        return "검사 범위 기록 없음"  # 건너뛴 검사를 적기 전의 기록. 확인 없이 내보내지 않는다
    return "·".join(skipped)


def refine_cost_by_day(cfg):
    """날짜별 검수 비용(USD). 검수기 예산(REFINE_TOTAL_BUDGET_USD)은 실행 폴더마다 따로 세므로 여기서 날짜로 합친다"""
    days = {}
    for f in sorted((cfg.work / "runs").glob("*/out_*/cost.json")):
        name = f.parent.parent.name  # 실행 폴더 이름 YYYYMMDD-HHMMSS
        try:
            usd = float(json.loads(f.read_text(encoding="utf-8")).get("cost_usd") or 0)
        except (OSError, ValueError, AttributeError):
            continue
        day = f"{name[:4]}-{name[4:6]}-{name[6:8]}"
        days[day] = round(days.get(day, 0) + usd, 4)
    return days


def refine_cost_note(cfg):
    """오늘 검수 비용과, 기준을 넘었으면 경고 한 줄"""
    today = refine_cost_by_day(cfg).get(time.strftime("%Y-%m-%d"), 0)
    note = {"today_usd": today, "warn_usd": cfg.refine_warn_usd}
    if cfg.refine_warn_usd and today >= cfg.refine_warn_usd:
        note["warning"] = f"오늘 검수 비용 ${today:.2f}이 기준 ${cfg.refine_warn_usd:.2f}을 넘었습니다. 상한은 API 키 콘솔의 지출 한도로 겁니다"
    return note


def local_release_status(cfg, it):
    """자동 생성 대본이면 생성기 출력 최신본의 release_status. 없거나 읽을 수 없으면 빈 문자열"""
    if not it.get("file"):
        return ""
    try:
        return json.loads(output_path(cfg, it).read_text(encoding="utf-8")).get("release_status") or ""
    except (OSError, ValueError, AttributeError):
        return ""


def publish(cfg, state, studio, dry):
    """⑤ 음성 완료 대본 가운데 받아쓰기 대조를 통과한 것을 사람 청취 없이 게시한다 (TEUM_AUTO_PUBLISH).

    대조에서 걸린 음원은 게시하지 않고 사람에게 넘긴다. 대조 결과가 아직 없으면 다음 바퀴에 다시 본다.
    LLM 검사나 안전 검사를 건너뛴 채 통과한 대본(TEUM_REFINE_RULES_ONLY, TEUM_KANANA_URL 없음)과
    사람 승인으로 남기기로 한 분류(TEUM_AUTO_PUBLISH_HOLD_TOPICS)의 대본도 게시하지 않는다.
    """
    if cfg.auto_publish not in ("own", "all"):
        return {"enabled": False}
    ready, waiting, held, unchecked, topic_hold, release_hold = [], [], [], {}, [], {}
    for cid, s in sorted(studio.scripts().items()):
        it = state.get(cid) or {}
        if s.get("stage") != 4 or s.get("state") != "tts_done" or (cfg.auto_publish == "own" and not it.get("own")):
            continue
        stt = s.get("stt") or {}
        missed = unchecked_checks(it)
        rs = s.get("release_status") or local_release_status(cfg, it)
        if rs:
            release_hold[cid] = rs  # 생성기가 공개 전 검토 대기(pending_*)나 차단(blocked)으로 표시한 대본. 사이트도 게시를 거부한다
        elif missed:
            unchecked[cid] = missed  # 검사를 건너뛴 채 통과한 대본은 자동으로 내보내지 않는다
        elif s.get("topic_code") in cfg.publish_hold_topics:
            topic_hold.append(cid)  # 사람 승인으로 남기기로 한 분류
        elif "cer" not in stt:
            waiting.append(cid)  # 받아쓰기 대조 전이거나 대조가 실패함. 확인 없이 게시하지 않는다
        elif stt.get("flag"):
            held.append(cid)
            if not dry and it.get("publish_stop") != stt.get("ts"):
                studio.comment(cid, f"[자동 게시 보류] 받아쓰기 대조에서 대본과 {stt['cer']:.1%} 달라 사람이 듣고 판정해야 합니다")
                state.note(cid, publish_stop=stt.get("ts"))
        else:
            ready.append(cid)
    ready = ready[:cfg.max_publish]
    out = {"enabled": True, "scope": cfg.auto_publish, "waiting_check": waiting, "held": held,
           "skipped_check_pass": unchecked, "topic_hold": topic_hold, "release_hold": release_hold}
    if dry:
        return {**out, "would_publish": ready}
    if ready:
        res = studio.add_to_app(ready)
        for cid in res.get("added", []):
            state.note(cid, published=time.strftime("%Y-%m-%d %H:%M:%S"))
        out["api_restart"] = res.get("api_restart")
    return {**out, "published": ready}


def tick(cfg, state, studio, dry=False):
    """한 바퀴. 단계마다 결과를 모아 돌려준다"""
    ensure_layout(cfg)
    report = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "dry_run": dry}
    try:  # 제작진 피드백 → 프롬프트 옵티마이저(매일 TEUM_OPTIMIZE_AT). 팀 프롬프트 판은 생성기 작업이 비었을 때 켠다
        from . import feedback
        fb = feedback.step(cfg, dry, len(pending_jobs(cfg)))
        if fb:
            report["0_feedback"] = fb
    except Exception as e:  # noqa: BLE001  피드백 단계가 실패해도 생산은 돈다
        report["0_feedback"] = {"error": f"{type(e).__name__}: {e}"[:300]}
    if not dry and not cfg.generator_blocker():
        write_sa_config(cfg)  # 수정(revise)만 도는 바퀴에도 같은 모델 설정이 적용되게
    report["1_collect"] = collect(cfg, dry)
    report["2_generate"] = generate(cfg, dry)
    try:
        report["2_submit"] = submit(cfg, state, studio, dry)
        rounds = []
        for _ in range(cfg.max_rounds + 1):  # 검수 → 수정 → 재검수를 한 바퀴 안에서 끝까지
            r = {"refine": refine(cfg, state, studio, dry)}
            r["revise"] = revise(cfg, state, studio, dry)
            rounds.append(r)
            state.save()
            if dry or not r["revise"]["revised"]:
                break
        report["3_review"] = rounds
        report["5_publish"] = publish(cfg, state, studio, dry)  # ④ 음성 합성은 스튜디오가 검수 통과 직후 스스로 돈다
        report["refine_cost"] = refine_cost_note(cfg)
    except StudioError as e:
        report["error"] = str(e)
    if not dry:
        state.save()
    return report
