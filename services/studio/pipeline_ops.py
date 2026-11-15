"""관리 대시보드가 팀 제작 파이프라인(ai/content, 팀 코드 그대로)을 한 편 단위로 부르는 얇은 연결 (2026-11-07).

실행: python services/studio/pipeline_ops.py <명령> ...  → 표준 출력에 JSON 한 줄
  ingest <md>                       대본 md 넣기(형식 검사 결과와 상태)
  attach <cid> <mp3> <voice_id>     Typecast mp3를 그 편의 새 음원 버전으로 붙이기(jobs → wav → tts-ingest와 같은 절차)
  review <cid> approve|reject <메모>  사람 검수
  publish                           승인·게시본을 data_root에 게시(catalog.sqlite + mp3)
  status                            전 편 상태
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "ai/content/src"))
from teum_content.pipeline import Pipeline, parse  # noqa: E402

HOME = Path(os.environ.get("TEUM_TEAM_RUN") or os.environ.get("TEUM_HOME") or REPO / "var")
WORK = HOME / "work_pipeline"
DATA_ROOT = Path(os.environ.get("TEUM_DATA_ROOT", HOME / "data_root"))


def row(p, cid):
    r = p.item(cid)
    return dict(r) if r else None


def main(cmd, args):
    p = Pipeline(WORK)
    try:
        return run(p, cmd, args)
    finally:
        p.db.commit()  # 11/10: 팀 CLI(__main__.py)처럼 저장. 없으면 대시보드에서 올린 대본·음원이 파이프라인에 남지 않았다


def run(p, cmd, args):
    if cmd == "ingest":
        p.ingest([args[0]])
        d = parse(args[0])
        return {"content_id": d["content_id"], "error": d["error"], "warnings": d["warnings"], "item": row(p, d["content_id"])}
    if cmd == "attach":
        cid, mp3, voice_id = args
        r = row(p, cid)
        if r is None:
            raise ValueError("파이프라인에 없는 대본: 먼저 올리세요")
        if r["state"] in ("tts_done", "approved", "published", "rejected", "failed"):
            p.redo(cid, f"새 음원 붙이기 ({voice_id})")
        r = row(p, cid)
        if r["state"] not in ("script_ready", "tts_failed"):
            raise ValueError(f"음원을 붙일 수 없는 상태: {r['state']}")
        version = r["audio_version"] + 1
        wav = str(WORK / cid / f"audio.v{version}.wav")
        p.update(cid, state="tts_pending", audio_version=version, wav=wav, duration_sec=None)
        p.log(cid, "tts-jobs", detail=f"{wav} (typecast {voice_id})")
        res = {"content_id": cid, "ok": False, "wav": wav, "duration_sec": None}
        q = subprocess.run([os.environ.get("TEUM_FFMPEG", "ffmpeg"), "-y", "-loglevel", "error", "-i", mp3, "-ac", "1", "-ar", "24000", wav], capture_output=True, text=True)
        if q.returncode:
            res["error"] = q.stderr.strip()[-300:]
        else:
            res["ok"] = True
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, dir=WORK) as f:
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
        p.results(f.name)
        Path(f.name).unlink()
        return {"item": row(p, cid)}
    if cmd == "review":
        cid, verdict, note = args
        p.review(cid, verdict == "approve", note)
        return {"item": row(p, cid)}
    if cmd == "publish":
        # 11/14: A100에서 옮겨 온 게시본 88편은 DB 행만 있고 작업 폴더(script.json·wav)는 옮기지 않았다.
        # 팀 publish는 게시본도 다시 쓰므로 그 편들에서 멈췄다. 파일이 없는 게시본은 카탈로그에 이미 있으니 건너뛴다.
        # 승인 대기(approved)인데 파일이 없으면 그대로 오류를 낸다.
        def has_files(r):
            return bool(r["wav"]) and Path(r["wav"]).exists() and (WORK / r["content_id"] / "script.json").exists()
        every = p.items
        skipped = [r["content_id"] for r in every() if r["state"] == "published" and not has_files(r)]
        p.items = lambda: [r for r in every() if r["content_id"] not in skipped]
        try:
            p.publish(DATA_ROOT)
        finally:
            p.items = every
        return {"published": [r["content_id"] for r in every() if r["state"] == "published"], "skipped_no_files": len(skipped)}
    if cmd == "status":
        return {"items": [dict(r) for r in p.items()]}
    raise ValueError("모르는 명령")


if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, **main(sys.argv[1], sys.argv[2:])}, ensure_ascii=False))
    except Exception as e:  # 대시보드에 그대로 보여 줄 오류
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
