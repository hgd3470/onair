"""롱폼(두 사람 대담) 목록과 음원. ai/longform/pipeline.py가 만든 out/<ID>/ 폴더를 그대로 읽는다 (11/14).

음성까지 끝난 회차(episode.mp3가 있는 것)만 내준다. 차량 앱 홈의 '롱폼' 줄이 쓴다.
"""
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter(tags=["longform"])
LF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def episodes(out_dir: Path | None, api_prefix: str):
    if not out_dir or not out_dir.is_dir():
        return []
    items = []
    for d in sorted(p for p in out_dir.iterdir() if p.is_dir() and LF_ID.match(p.name)):
        script = (_read(d / "script.json") or {})
        ep = _read(d / "episode.json") or {}
        if not (d / "episode.mp3").is_file() or not script.get("script"):
            continue
        lines = script["script"].get("lines") or []
        show = script.get("show") or []
        items.append({
            "content_id": f"lf-{d.name}", "kind": "longform", "title": script["script"].get("title") or d.name,
            "hosts": show[:2], "hook": next((x.get("text", "") for x in lines if x.get("speaker") == "B"), ""),
            "duration_sec": ep.get("duration_sec"), "audio_uri": f"{api_prefix}/longform/{d.name}.mp3",
        })
    return items


def _out_dir(request: Request):
    return request.app.state.settings.longform_out


@router.get("/longform")
def list_longform(request: Request):
    items = episodes(_out_dir(request), request.app.state.settings.api_prefix)
    return {"count": len(items), "items": items}


@router.get("/longform/{pid}.mp3", include_in_schema=False)
def longform_audio(pid: str, request: Request):
    out = _out_dir(request)
    f = out / pid / "episode.mp3" if out and LF_ID.match(pid) else None
    if f is None or not f.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(f, media_type="audio/mpeg")
