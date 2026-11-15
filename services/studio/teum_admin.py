"""온에어 관리 대시보드 (studio.onaircar.com/admin/, 2026-11-07).

팀원별 계정으로 로그인해 목소리 라인업 · 대본 · 원천을 한 화면에서 고친다. 고친 내용은 서버 SQLite에 저장되고
누가 언제 무엇을 바꿨는지 기록이 남는다. 코드에서는 /admin/export/<표>.csv|.json 을 Basic 인증으로 받는다.
  curl -u <이름이나 영문 아이디>:<공용 비밀번호> https://studio.onaircar.com/admin/export/scripts.csv

- 계정: admin_data/users.json (아이디, 이름, 역할). 로그인은 이름 + 팀 공용 비밀번호(admin_data/shared.json 해시).
  공용 비밀번호 바꾸기: admin_users.py shared (표준 입력으로 받음)
- 칸별 담당: FIELDS. 표시용이고 누구나 다 고칠 수 있다. 누가 무엇을 바꿨는지와 의견은 log에 남는다
- 첫 실행 때 DB가 비어 있으면 TEUM_ADMIN_SEED(seed.json)를 넣는다
- 음원: TEUM_ADMIN_AUDIO 폴더의 <content_id>__<voice_id>.mp3 를 /admin/audio/ 로 내준다(로그인 뒤에만)
"""
import asyncio
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import sys
import sqlite3
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from teum_admin_hash import pw_hash

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # 저장소 루트 (services/studio 기준)
HOME = Path(os.environ.get("TEUM_HOME", REPO / "var"))  # 바뀌는 데이터(DB·대본·음원)는 모두 여기. 저장소 밖 경로를 권한다
DATA = Path(os.environ.get("TEUM_ADMIN_DATA", HOME / "admin_data"))
SEED = Path(os.environ.get("TEUM_ADMIN_SEED", HERE / "seed.json"))
# 팀 제작 파이프라인과 팀 API
TEAM = Path(os.environ.get("TEUM_TEAM_RUN", HOME))  # scripts_live, work_pipeline, audio_live, data_root가 놓이는 곳
OPS = [os.environ.get("TEUM_PYTHON", sys.executable), str(HERE / "pipeline_ops.py")]
SCRIPTS_LIVE = TEAM / "scripts_live"
PIPE_DB = TEAM / "work_pipeline" / "pipeline.sqlite"
AUDIO = Path(os.environ.get("TEUM_ADMIN_AUDIO", str(TEAM / "audio_live")))
DAILY_CREDITS = int(os.environ.get("TEUM_DAILY_CREDITS", "5000"))  # 하루 Typecast 합성 한도(글자 수 = 크레딧)
STATE_KO = {"script_ready": "음원 필요", "invalid": "대본 오류", "tts_pending": "음원 만드는 중", "tts_done": "검수 대기",
            "tts_failed": "음원 실패", "failed": "음원 실패", "approved": "게시 대기", "rejected": "반려", "published": "게시됨"}
PAGE = HERE / "admin_static" / "index.html"
# 우리 대본 음원이 아직 없는 목소리는 Typecast 공식 샘플 주소로 들려준다
try:
    _vs = json.loads((HERE / "voices.json").read_text())
    PREVIEW = {x["voice_id"]: x.get("preview_url", "") for x in (_vs if isinstance(_vs, list) else _vs.get("voices", []))}
except Exception:
    PREVIEW = {}
IMG = Path(os.environ.get("TEUM_ADMIN_IMG", HOME / "voice_img"))  # 성우 얼굴(Typecast 스튜디오 캡처)
KEY = os.environ["TEUM_COOKIE_KEY"].encode()
COOKIE = "teum_admin"
TABLES = ("voices", "scripts", "sources")

# 칸별 담당 역할 (2차 스프린트 캔버스 F0CFGDVHSHL 담당 기준). admin은 전부
FIELDS = {
    "voices": {"role": "voice", "tc_category": "voice", "memo": "voice"},
    "scripts": {"owner": "script", "title": "script", "script_status": "script", "script_memo": "script",
                "voice": "voice", "pick2": "voice", "voice_status": "voice", "voice_reason": "voice",
                "refine_status": "refine", "refine_memo": "refine", "app_status": "app", "body": "script", "source_note": "script"},
    "sources": {"grade": "rights", "credit": "rights", "memo": "rights",
                "rights_check": "rights_check", "usage_memo": "script"},
}
lock = threading.Lock()


def db():
    DATA.mkdir(exist_ok=True)
    c = sqlite3.connect(DATA / "admin.db", check_same_thread=False)
    c.execute("create table if not exists items (tbl text, id text, pos integer, data text, updated_by text, updated_at real, primary key (tbl, id))")
    c.execute("create table if not exists log (ts real, user text, tbl text, id text, field text, old text, new text)")
    c.execute("create table if not exists ab (ts real, user text, script_id text, a text, b text, choice text, why text)")
    c.execute("create table if not exists synth (ts real, user text, script_id text, voice text, chars integer)")
    if not c.execute("select 1 from items limit 1").fetchone() and SEED.exists():
        seed = json.loads(SEED.read_text())
        for t in TABLES:
            for i, row in enumerate(seed[t]):
                c.execute("insert into items values (?,?,?,?,?,?)", (t, row["id"], i, json.dumps(row, ensure_ascii=False), "seed", time.time()))
    # 나중에 seed에 생긴 칸(본문·출처 등)은 기존 줄에 비어 있을 때만 채운다. 사람이 고친 값은 건드리지 않는다
    if SEED.exists():
        seed = json.loads(SEED.read_text())
        for t in TABLES:
            for row in seed[t]:
                r = c.execute("select data from items where tbl=? and id=?", (t, row["id"])).fetchone()
                if r:
                    cur = json.loads(r[0])
                    add = {k: v for k, v in row.items() if k not in cur}
                    if add:
                        c.execute("update items set data=? where tbl=? and id=?", (json.dumps({**cur, **add}, ensure_ascii=False), t, row["id"]))
    c.commit()
    return c


CON = db()


def users():
    return json.loads((DATA / "users.json").read_text())


def shared():
    """팀 공용 비밀번호(admin_data/shared.json). 있으면 개인 비밀번호 대신 이것만 받는다"""
    f = DATA / "shared.json"
    return json.loads(f.read_text()) if f.exists() else None


def secret(u):
    s = shared()
    return s["hash"] if s else u["hash"]


def resolve(name):
    """화면 이름(운영, 검수 …)이나 영문 아이디를 계정 아이디로"""
    name = name.strip().replace(" ", "")
    for uid, u in users().items():
        if name and name in (uid, u["name"], u["name"][1:] if len(u["name"]) >= 3 else uid):
            return uid, u
    return None, None


def check_pw(name, pw):
    uid, u = resolve(name)
    if not u:
        return None, None
    s = shared() or u
    return (uid, u) if hmac.compare_digest(pw_hash(pw, s["salt"]), s["hash"]) else (None, None)


def mac_of(uid, exp, u):
    return hmac.new(KEY, f"admin:{uid}:{exp}:{secret(u)}".encode(), hashlib.sha256).hexdigest()


def sign(uid, u):
    exp = str(int(time.time()) + 30 * 86400)
    return f"{uid}.{exp}.{mac_of(uid, exp, u)}"


def who(request):
    """쿠키나 Basic 인증으로 로그인한 사람. 없으면 None"""
    svc = os.environ.get("TEUM_SERVICE_TOKEN", "")
    if svc and hmac.compare_digest(request.headers.get("x-teum-service", "").encode(), svc.encode()):
        return {"id": "auto", "name": "자동", "roles": []}  # 제작 자동화. 변경 기록에는 '자동'으로 남는다
    tok = request.cookies.get(COOKIE, "")
    if tok.count(".") == 2:
        uid, exp, mac = tok.split(".")
        u = users().get(uid)
        if u and exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(mac, mac_of(uid, exp, u)):
            return {"id": uid, **u}
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            name, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
        except Exception:
            return None
        uid, u = check_pw(name, pw)
        if u:
            return {"id": uid, **u}
    return None


def can(u, tbl, field):
    """로그인한 사람은 누구나 모든 칸을 고친다. 담당 표시는 FIELDS로만 하고, 변경은 log에 다 남는다"""
    return field in FIELDS.get(tbl, {})


def mine(u, tbl, field):
    return FIELDS.get(tbl, {}).get(field) in u.get("roles", [])


def rows(tbl):
    return [json.loads(d) for (d,) in CON.execute("select data from items where tbl=? order by pos", (tbl,))]


def audio_for(cid, vid):
    f = AUDIO / f"{cid}__{vid}.mp3"
    return f"audio/{f.name}" if f.exists() else ""


LOGIN = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>온에어 관리</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f7f9;color:#16181d;font:15px/1.5 system-ui,"Apple SD Gothic Neo",sans-serif;padding:16px}
form{width:100%;max-width:320px;background:#fff;border:1px solid #e3e6ea;border-radius:12px;padding:22px;display:grid;gap:10px}
h1{font-size:19px;margin:0}label{font-size:13px;color:#5b616b}input{font:inherit;padding:10px;border:1px solid #c9ced6;border-radius:8px}
button{font:inherit;font-weight:600;padding:11px;border:0;border-radius:8px;background:#1d4ed8;color:#fff}.err{color:#b91c1c;font-size:13px;margin:0}
</style></head><body><form method="post" action="/admin/login"><h1>온에어 관리</h1>{msg}
<label for="u">아이디</label><input id="u" name="user" autocomplete="username" placeholder="예: 운영" autocapitalize="off" autocorrect="off" spellcheck="false" required autofocus>
<label for="p">비밀번호</label><input id="p" name="password" type="password" autocomplete="off" autocapitalize="off" required>
<button type="submit">로그인</button></form></body></html>"""


# 로그인 잠금 없음 (팀 결정)


def _next(v):
    """청취 웹앱(/app/)에서 넘어온 로그인만 그쪽으로 되돌려 보낸다 (11/10)"""
    return "/app/" if str(v or "") == "/app/" else ""


def _login_page(msg, nxt, status=200):
    hidden = '<input type="hidden" name="next" value="/app/">' if nxt else ""
    return HTMLResponse(LOGIN.replace("{msg}", msg + hidden), status)


async def login(request: Request):
    if request.method == "POST":
        form = await request.form()
        nxt = _next(form.get("next"))
        typed = str(form.get("user", "")).strip()
        uid, u = check_pw(typed, str(form.get("password", "")))
        if u:
            r = RedirectResponse(nxt or "/admin/", 303)
            # 11/10: 쿠키 경로를 /admin → / 로. 청취 웹앱(/app/)과 팀 API(/api/v1)도 같은 로그인으로 쓴다
            r.set_cookie(COOKIE, sign(uid, u), max_age=30 * 86400, httponly=True, secure=True, samesite="lax", path="/")
            return r
        pw_ = str(form.get("password", ""))
        print(f"admin login fail: name={typed!r} known={resolve(typed)[0] is not None} pw_len={len(pw_)} pw_ascii={pw_.isascii()}", flush=True)
        return _login_page('<p class="err">이름이나 비밀번호가 맞지 않습니다.</p>', nxt, 401)
    return _login_page("", _next(request.query_params.get("next")))


async def logout(request):
    r = RedirectResponse("/admin/login", 303)
    r.delete_cookie(COOKIE, path="/admin")
    r.delete_cookie(COOKIE, path="/")
    return r


async def page(request):
    if not who(request):
        return RedirectResponse("/admin/login", 303)
    return FileResponse(PAGE, headers={"cache-control": "private, no-store"})


def need(request):
    u = who(request)
    if not u:
        raise PermissionError
    return u


async def api_data(request):
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    ensure_auto()
    scripts = rows("scripts")
    vid = {v["name"]: v["voice_id"] for v in rows("voices")}
    for s in scripts:
        s["hold_src"] = hold_src(s)
        s["audio"] = audio_for(s["id"], vid.get(s["voice"], ""))
        s["audio2"] = audio_for(s["id"], vid.get(s.get("pick2", ""), "")) if s.get("pick2") else ""
    st = pipe_states()
    for s in scripts:
        p = st.get(s["id"], {})
        s["release_status"] = release_status(s["id"])
        s["state"] = p.get("state", "")
        s["state_ko"] = STATE_KO.get(p.get("state", ""), "파이프라인에 없음")
        s["last_error"] = p.get("last_error") or ""
        s["duration_sec"] = p.get("duration_sec") or ""
        s["chars"] = len((s.get("body") or "").replace("\n", ""))
        s.setdefault("owner", "script")
        s["stage"] = stage(s, st)
    dropped = [{"id": s["id"], "title": s.get("title", ""), "owner": s.get("owner", ""), "by": s.get("drop_by", ""),
                "at": s.get("drop_at", 0), "reason": s.get("drop_reason", "")} for s in scripts if s["stage"] == 0]
    scripts = [s for s in scripts if s["stage"] != 0]
    voices = rows("voices")
    for v in voices:
        v["img"] = f"img/{v['voice_id']}.webp" if (IMG / f"{v['voice_id']}.webp").exists() else ""
        v["sample"] = next((s["audio"] for s in scripts if s["voice"] == v["name"] and s["audio"]), "") or PREVIEW.get(v["voice_id"], "")
    editable = {t: [f for f in FIELDS[t] if can(u, t, f)] for t in TABLES}
    my = {t: [f for f in FIELDS[t] if mine(u, t, f)] for t in TABLES}
    log = [dict(zip(("ts", "user", "tbl", "id", "field", "old", "new"), r))
           for r in CON.execute("select * from log order by ts desc limit 200")]
    ab = [dict(zip(("ts", "user", "script_id", "a", "b", "choice", "why"), r))
          for r in CON.execute("select * from ab order by ts desc limit 500")]
    names = {k: v["name"] for k, v in users().items()}
    names["auto"] = "자동"
    return JSONResponse({"me": {"id": u["id"], "name": u["name"], "roles": u["roles"]}, "names": names,
                         "editable": editable, "mine": my, "voices": voices, "scripts": scripts,
                         "sources": rows("sources"), "dropped": dropped, "log": log, "ab": ab, "credits": credits(),
                         "jobs": {k: JOBS[k] for k in ("running", "queue", "done", "failed", "by")},
                         "auto": {"on": AUTO_ON, "last": AUTO["last"], "error": AUTO["error"]}})


async def api_update(request):
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    b = await request.json()
    tbl, rid, field, value = b.get("tbl"), str(b.get("id")), b.get("field"), str(b.get("value", ""))[:20000]
    if tbl not in TABLES or not can(u, tbl, field):
        return JSONResponse({"detail": "고칠 수 없는 칸입니다."}, 403)
    with lock:
        r = CON.execute("select data from items where tbl=? and id=?", (tbl, rid)).fetchone()
        if not r:
            return JSONResponse({"detail": "없는 항목"}, 404)
        row = json.loads(r[0])
        old = str(row.get(field, ""))
        if old != value:
            row[field] = value
            if tbl == "scripts" and field in ("body", "voice") and pipe_states().get(rid, {}).get("audio_version"):
                row["audio_stale"] = "본문이나 목소리가 바뀜 · 음원 다시 만들어야 함"
            if tbl == "scripts" and field in ("body", "title") and (row.get("refine_status") == "통과" or row.get("script_status") == "제출"):
                row["refine_status"], row["script_status"] = "", "초안"
                CON.execute("insert into log values (?,?,?,?,?,?,?)", (time.time(), u["id"], tbl, rid, "단계", "", "본문·제목이 바뀌어 다시 제출·검수 필요"))
            if tbl == "scripts" and field == "voice":
                vid = {v["name"]: v["voice_id"] for v in rows("voices")}
                if value not in vid:
                    return JSONResponse({"detail": "라인업에 없는 목소리"}, 400)
                row["voice_id"] = vid[value]
            now = time.time()
            CON.execute("update items set data=?, updated_by=?, updated_at=? where tbl=? and id=?",
                        (json.dumps(row, ensure_ascii=False), u["id"], now, tbl, rid))
            CON.execute("insert into log values (?,?,?,?,?,?,?)", (now, u["id"], tbl, rid, field, old, value))
            CON.commit()
    if tbl == "scripts" and field in ("body", "source_note", "title") and old != value and row.get("script_file"):
        f = SCRIPTS_LIVE / row["script_file"]
        if f.exists():
            f.write_text(md_apply(f.read_text(encoding="utf-8"), field, value), encoding="utf-8")
            res = await ops("ingest", f)
            if not res.get("ok") or res.get("error"):
                return JSONResponse({"ok": True, "row": row, "warn": "파이프라인 반영 실패: " + str(res.get("error"))})
    if tbl == "scripts" and field in ("refine_status", "voice") :
        kick_auto()
    return JSONResponse({"ok": True, "row": row})


async def api_comment(request):
    """항목에 의견 남기기. 변경 기록(log)에 field='의견'으로 같이 쌓인다"""
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    b = await request.json()
    tbl, rid, text = b.get("tbl"), str(b.get("id")), str(b.get("text", "")).strip()[:2000]
    if tbl not in TABLES or not text:
        return JSONResponse({"detail": "내용이 비었습니다"}, 400)
    with lock:
        CON.execute("insert into log values (?,?,?,?,?,?,?)", (time.time(), u["id"], tbl, rid, "의견", "", text))
        CON.commit()
    return JSONResponse({"ok": True})


async def api_ab(request):
    """A/B 듣기 판정. 판정은 누구나 남길 수 있다. 목소리 바꾸기는 voice 담당이 따로 한다"""
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    b = await request.json()
    if b.get("choice") not in ("A", "B", "넘기기", "빼기"):
        return JSONResponse({"detail": "choice"}, 400)
    with lock:
        CON.execute("insert into ab values (?,?,?,?,?,?,?)", (time.time(), u["id"], str(b.get("script_id")),
                    str(b.get("a", "")), str(b.get("b", "")), b["choice"], str(b.get("why", ""))[:1000]))
        CON.commit()
    return JSONResponse({"ok": True})


async def export(request):
    if not who(request):
        return Response("login required", 401, headers={"www-authenticate": 'Basic realm="teum-admin"'})
    name, _, ext = request.path_params["name"].rpartition(".")
    if name == "audio" and ext == "zip":
        return audio_zip()
    if name == "ab":
        data = [dict(zip(("ts", "user", "script_id", "a", "b", "choice", "why"), r)) for r in CON.execute("select * from ab order by ts")]
    elif name == "log":
        data = [dict(zip(("ts", "user", "tbl", "id", "field", "old", "new"), r)) for r in CON.execute("select * from log order by ts")]
    elif name in TABLES:
        data = rows(name)
    else:
        return Response("unknown", 404)
    hdr = {"cache-control": "private, no-store", "content-disposition": f'attachment; filename="teum_{name}.{ext}"'}
    if ext == "json":
        return Response(json.dumps(data, ensure_ascii=False, indent=1), media_type="application/json", headers=hdr)
    if ext == "csv":
        buf = io.StringIO()
        keys = list(dict.fromkeys(k for d in data for k in d))
        w = csv.DictWriter(buf, keys)
        w.writeheader()
        w.writerows(data)
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers=hdr)
    return Response("unknown", 404)


def audio_zip():
    """지금 배정된 목소리 기준 대본 음원 묶음(mp3는 이미 압축돼 있어 저장만). 파일 이름: <대본ID>__<목소리>.mp3"""
    vid = {v["name"]: v["voice_id"] for v in rows("voices")}
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False, dir=DATA)  # 루트 디스크가 작아 /tmp 대신
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        lines = ["content_id,title,voice,file"]
        for s in rows("scripts"):
            f = AUDIO / f"{s['id']}__{vid.get(s['voice'], '')}.mp3"
            if f.exists():
                arc = f"{s['id']}__{s['voice']}.mp3"
                z.write(f, arc)
                lines.append(f"{s['id']},\"{s['title']}\",{s['voice']},{arc}")
        z.writestr("목록.csv", "\ufeff" + "\n".join(lines) + "\n")
        z.writestr("README.txt", "온에어 대본 음원 (AI 음성, Typecast). 팀 내부 시연용. 사람 검수 전 음원이 섞여 있다.\n")
    tmp.close()
    stamp = time.strftime("%Y%m%d_%H%M")
    return FileResponse(tmp.name, media_type="application/zip", filename=f"teum_audio_{stamp}.zip",
                        headers={"cache-control": "private, no-store"}, background=BackgroundTask(os.unlink, tmp.name))


async def audio(request):
    if not who(request):
        return Response("login required", 401)
    f = (AUDIO / request.path_params["name"]).resolve()
    if f.parent != AUDIO.resolve() or f.suffix != ".mp3" or not f.exists():
        return Response("not found", 404)
    return FileResponse(f, headers={"cache-control": "private, max-age=600"})


async def img(request):
    if not who(request):
        return Response("login required", 401)
    f = (IMG / request.path_params["name"]).resolve()
    if f.parent != IMG.resolve() or f.suffix != ".webp" or not f.exists():
        return Response("not found", 404)
    return FileResponse(f, media_type="image/webp", headers={"cache-control": "private, max-age=3600"})


# ---- 업무 순서: 대본 쓰기 → 제출 → 대본 검수 통과 → (관리자) 음성 만들기 → 듣고 앱에 추가 ----
def stage(s, st):
    if s.get("script_status") == "제외":  # 11/10: 드랍한 대본은 어느 단계에도 없다 (화면·꾸러미·자동화에서 빠짐)
        return 0
    p = st.get(s["id"], {}).get("state", "")
    if s.get("audio_stale") and s.get("refine_status") == "통과":
        return 3
    if p == "published" and not s.get("audio_stale"):
        return 5
    if p in ("tts_done", "approved") and not s.get("audio_stale"):
        return 4
    if s.get("refine_status") == "통과":
        return 3
    if s.get("script_status") == "제출":
        return 2
    return 1


JOBS = {"running": False, "queue": [], "done": [], "failed": [], "by": "", "started": 0}

# ---- 검수 뒤 자동화 (생성·검수가 끝난 뒤는 자동) ----
# 검수 통과 → 주제 기본 목소리 배정 → 합성(하루 한도 안) → 파이프라인 등록 → 받아쓰기 대조. 듣고 게시는 사람.
AUTO_ON = os.environ.get("TEUM_AUTO_VOICE", "0") == "1"  # 켜면 검수 통과 대본에 목소리를 배정해 바로 합성한다(Typecast 비용). 기본은 꺼짐
AUTO_VOICE = {  # 주제 코드 → 기본 목소리. 11/7 37편 배정에서 주제별로 가장 많이 쓴 목소리와 라인업 역할표 기준
    "C01": "다은", "C02": "다은", "C03": "강일", "C04": "다은", "C05": "서현", "C06": "서현", "C07": "다은",
    "C08": "필재", "C09": "다은", "C10": "서현", "C11": "강일", "C12": "다은", "C13": "서현", "C14": "주하",
    "C15": "주하", "C16": "필재", "C17": "강일", "C18": "강일", "C19": "박창수", "C20": "필재", "C21": "성호"}
AUTO = {"started": False, "ticking": False, "last": 0, "fail": {}, "error": ""}
STT_PY = [os.environ.get("TEUM_STT_PYTHON", sys.executable), str(HERE / "teum_stt.py")]  # torch·transformers가 있는 파이썬
STT_LIMIT = 0.03  # 받아쓰기 불일치 3% 넘으면 '받아쓰기 확인' (teum_stt.py 머리말)
_STT_LOCK = []


# ---- 팀 파이프라인 연결 ----
def pipe_states():
    if not PIPE_DB.exists():
        return {}
    con = sqlite3.connect(f"file:{PIPE_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = {r["content_id"]: dict(r) for r in con.execute("select * from item")}
    con.close()
    return out


async def ops(*args):
    p = await asyncio.create_subprocess_exec(*OPS, *map(str, args), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate()
    try:
        return json.loads(out.decode().strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "error": (err.decode() or out.decode())[-500:]}


_CRED = {"t": 0, "left": None}


def credits():
    today = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    used = CON.execute("select coalesce(sum(chars),0) from synth where ts>=?", (today,)).fetchone()[0]
    return {"today_used": used, "today_limit": DAILY_CREDITS, "typecast_left": _CRED["left"]}


def tc_mod():
    import typecast_client as tc  # 키는 TYPECAST_API_KEY 또는 TYPECAST_ENV 파일에서 읽는다(출력 금지)
    tc.UA = "teum-admin/1 typecast-integration/1 (source=api-page; generated_by=claude-code)"
    return tc


def refresh_credit():
    try:
        c = tc_mod().sub()["credits"]
        _CRED.update(t=time.time(), left=c["plan_credits"] - c["used_credits"])
    except Exception:
        pass


def md_meta(text):
    meta = dict(re.findall(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*$", text.split("## 2.")[0], re.M))
    title = re.search(r"^# (.+)$", text, re.M)
    body = re.search(r"## 2\. 낭독 본문\s*```text\n(.*?)```", text, re.S)
    src = re.search(r"(## 4\..*?)(?=\n## |\Z)", text, re.S)
    code = re.search(r"\((C\d+)\)", meta.get("관심사·프로그램", ""))
    return {"id": meta.get("관리번호", "").strip().lower(), "title": title[1].strip() if title else "",
            "body": body[1].strip() if body else "", "source_note": src[1].strip() if src else "",
            "topic_code": code[1] if code else "", "topic_name": re.sub(r"\s*\(C\d+\)", "", meta.get("관심사·프로그램", "")).strip(),
            "group": meta.get("대분류", "").strip(), "format": meta.get("유형", "").strip(), "target_sec": meta.get("목표 길이", "").strip()}


def md_apply(text, field, value):
    """대시보드에서 고친 칸을 대본 md에 되써서 파이프라인이 같은 내용을 보게 한다"""
    if field == "body":
        return re.sub(r"(## 2\. 낭독 본문\s*```text\n)(.*?)(```)", lambda m: m[1] + value.strip() + "\n" + m[3], text, count=1, flags=re.S)
    if field == "source_note":
        return re.sub(r"## 4\..*?(?=\n## |\Z)", lambda m: value.strip(), text, count=1, flags=re.S)
    if field == "title":
        return re.sub(r"^# .+$", "# " + value.strip(), text, count=1, flags=re.M)
    return text


SAFE = re.compile(r"[^0-9A-Za-z가-힣._-]+")


def match_by_name(filename):
    """올린 파일 이름 → 기존 대본 id. 예전에 올린 파일 이름, 사이트 md 이름, '관리번호.json·.md' 순으로 찾는다"""
    low = str(filename or "").split("/")[-1].strip().lower()
    if not low:
        return None
    sc = rows("scripts")
    for s in sc:
        if low in ((s.get("upload_name") or "").lower(), (s.get("script_file") or "").lower()):
            return s["id"]
    stem = low.rsplit(".", 1)[0]
    return next((s["id"] for s in sc if s["id"] == stem), None)


async def put_md(u, filename, text, upload_name=None):
    """대본 md 한 편 넣기(새 대본이나 같은 관리번호 덮어쓰기) → 팀 파이프라인 ingest. (상태코드, 결과)
    다시 올릴 때(11/9): '수정 필요'였던 대본은 '제출 · 검수 대기'로 넘기고, 그 밖의 대본은 검수 상태와 단계를 그대로 둔다"""
    name = SAFE.sub("_", filename)[:120]
    if not name.endswith(".md"):
        name += ".md"
    m = md_meta(text)
    if not m["id"] or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", m["id"]):
        return 400, {"detail": "제작 정보 표의 관리번호가 없거나 형식이 틀립니다 (예: W3-C05-01)"}
    if not m["body"]:
        return 400, {"detail": "## 2. 낭독 본문의 ```text 블록이 비었습니다"}
    hit = match_by_name(upload_name) if upload_name else None
    if hit and hit != m["id"]:
        return 400, {"detail": f"파일 이름은 {hit.upper()} 대본인데 안의 관리번호는 {m['id'].upper()}입니다. 둘 중 하나를 맞춰 다시 올려 주세요"}
    prev_state = pipe_states().get(m["id"], {}).get("state", "")
    with lock:
        r = CON.execute("select data from items where tbl='scripts' and id=?", (m["id"],)).fetchone()
    if r and json.loads(r[0]).get("script_file"):
        name = json.loads(r[0])["script_file"]  # 같은 관리번호면 같은 파일을 덮어쓴다
    SCRIPTS_LIVE.mkdir(exist_ok=True)
    (SCRIPTS_LIVE / name).write_text(text, encoding="utf-8")
    res = await ops("ingest", SCRIPTS_LIVE / name)
    if not res.get("ok"):
        return 400, {"detail": res.get("error", "파이프라인 오류")}
    now = time.time()
    status = ""
    with lock:
        if r:
            row = json.loads(r[0])
            body_changed = any(m[k] and m[k] != str(row.get(k, "")) for k in ("title", "body"))
            for k in ("title", "body", "source_note", "topic_code", "topic_name", "group", "format", "target_sec"):
                if m[k] and str(row.get(k, "")) != m[k]:
                    CON.execute("insert into log values (?,?,?,?,?,?,?)", (now, u["id"], "scripts", m["id"], k, str(row.get(k, ""))[:300], m[k][:300]))
                    row[k] = m[k]
            row["script_file"] = name
            if upload_name:
                row["upload_name"] = str(upload_name).split("/")[-1]
            if row.get("refine_status") == "수정 필요":  # 고친 대본을 올리면 바로 다시 제출
                for k, v in (("script_status", "제출"), ("refine_status", "")):
                    CON.execute("insert into log values (?,?,?,?,?,?,?)", (now, u["id"], "scripts", m["id"], k, str(row.get(k, "")), v))
                    row[k] = v
                status = "수정본 재제출 · 검수 대기"
            else:
                status = "상태 유지" + ("" if body_changed else " · 본문 그대로")
            new_state = (res.get("item") or {}).get("state", "")
            if body_changed and prev_state in ("tts_done", "approved", "published") and new_state == "script_ready":
                status += " · 본문이 바뀌어 음원 다시 만들어야 함"
            CON.execute("insert into log values (?,?,?,?,?,?,?)", (now, u["id"], "scripts", m["id"], "다시 올리기", "", str(upload_name or name)))
            CON.execute("update items set data=?, updated_by=?, updated_at=? where tbl='scripts' and id=?",
                        (json.dumps(row, ensure_ascii=False), u["id"], now, m["id"]))
        else:
            pos = CON.execute("select coalesce(max(pos),0)+1 from items where tbl='scripts'").fetchone()[0]
            row = {**m, "script_file": name, "upload_name": str(upload_name or "").split("/")[-1], "owner": u["id"], "voice": "", "voice_id": "", "pick2": "", "rule_voice": "",
                   "voice_reason": "", "voice_status": "배정 전", "script_status": "초안", "script_memo": "",
                   "refine_status": "", "app_status": ""}
            CON.execute("insert into items values (?,?,?,?,?,?)", ("scripts", m["id"], pos, json.dumps(row, ensure_ascii=False), u["id"], now))
            CON.execute("insert into log values (?,?,?,?,?,?,?)", (now, u["id"], "scripts", m["id"], "올리기", "", name))
        CON.commit()
    return 200, {"ok": True, "id": m["id"], "new": not r, "status": status, "warnings": res.get("warnings", []), "error": res.get("error")}


# ---- 대본 JSON (11/8 팀 확정: 대본 생성기 출력, ai/script_agent/schema/handoff.schema.json) → 같은 md 양식으로 바꿔 넣는다 ----
CAT_CODES = {"C01": "일·직장", "C02": "연애·결혼", "C03": "가족·육아", "C04": "친구·인간관계", "C05": "마음·습관·성장",
             "C06": "자취·살림·생활팁", "C07": "음식·맛집·식문화", "C08": "돈·소비·생활경제", "C09": "건강·운동·생활리듬",
             "C10": "반려동물·자연", "C11": "여행·지역·문화", "C12": "패션·뷰티·자기표현", "C13": "과학·기술·AI",
             "C14": "역사·인물", "C15": "언어·독서·인문·가치관", "C16": "뉴스·사회·시사", "C17": "사건·미스터리·추리",
             "C18": "음악·영화·방송·예술", "C19": "스포츠·게임·취미", "C20": "자동차·교통·이동", "C21": "성경·말씀·신앙"}
CAT_GROUP = {**{c: "A. 사람과 일상" for c in ("C01", "C02", "C03", "C04", "C05")},
             **{c: "B. 생활과 취향" for c in ("C06", "C07", "C08", "C09", "C10", "C11", "C12")},
             **{c: "C. 지식과 세상" for c in ("C13", "C14", "C15", "C16", "C17")},
             **{c: "D. 문화·취미·이동" for c in ("C18", "C19", "C20")}, "C21": "E. 성경과 신앙"}  # server/teum/catalog/categories.py


def _cell(v):
    return re.sub(r"\s+", " ", str(v or "")).replace("|", "/").strip()


def json_to_md(d):
    """대본 JSON 한 편 → 관리 사이트·팀 파이프라인이 읽는 md. 필수: content_id, title, script"""
    if not isinstance(d, dict):
        raise ValueError("대본 JSON은 객체여야 합니다")
    cid = str(d.get("content_id") or "").strip()
    title = re.sub(r"\s+", " ", str(d.get("title") or "")).strip()
    script = d.get("script")
    if isinstance(script, str):
        lines = [x.strip() for x in script.splitlines() if x.strip()]
    elif isinstance(script, list):
        lines = [_cell(x.get("text") if isinstance(x, dict) else x) for x in script]
        lines = [x for x in lines if x]
    else:
        lines = []
    missing = [k for k, v in (("content_id", cid), ("title", title), ("script", lines)) if not v]
    if missing:
        raise ValueError("JSON에 " + ", ".join(missing) + " 가 없습니다")
    cat = str(d.get("category") or "").strip()
    code = cat.upper() if cat.upper() in CAT_CODES else next((c for c, n in CAT_CODES.items() if n == cat), "")
    tags = d.get("tags") or []
    sec = d.get("target_sec")
    srcs = [x for x in (d.get("sources") or []) if isinstance(x, dict)]
    meta = [("관리번호", cid.upper()), ("대분류", CAT_GROUP.get(code, "")),
            ("관심사·프로그램", f"{CAT_CODES[code]}({code})" if code else cat),
            ("유형", d.get("format") or d.get("production_type") or ""), ("제작 유형", d.get("production_type") or ""),
            ("세부 태그", ", ".join(map(str, tags)) if isinstance(tags, list) else tags),
            ("목표 길이", f"{int(sec)}초" if isinstance(sec, (int, float)) and sec else ""),
            ("대본 버전", d.get("version") or ""), ("생성기 상태", d.get("status") or ""),
            ("권리 상태", d.get("rights_status") or "")]
    out = [f"# {title}", "", "## 1. 제작 정보", "", "| 항목 | 내용 |", "|---|---|"]
    out += [f"| {k} | {_cell(v)} |" for k, v in meta if _cell(v)]
    out += ["", "## 2. 낭독 본문", "", "```text", *lines, "```", ""]
    note = d.get("adaptation_note") if isinstance(d.get("adaptation_note"), dict) else {}
    if note.get("from_source") or note.get("new"):
        out += ["## 3. 구성과 근거", ""]
        out += [f"- 원문 활용: {_cell(x)}" for x in note.get("from_source") or []]
        out += [f"- 새로 쓴 부분: {_cell(x)}" for x in note.get("new") or []]
        out.append("")
    out += ["## 4. 출처와 작성 범위", ""]
    if srcs:
        out += ["| 항목 | 내용 |", "|---|---|"]
        for i, s in enumerate(srcs, 1):
            lic = s.get("license") if isinstance(s.get("license"), dict) else {}
            name = _cell(s.get("title") or s.get("source_id") or s.get("author") or f"원천 {i}")
            url = str(s.get("source_url") or s.get("url") or "").strip()
            out.append(f"| 출처{'' if i == 1 else ' ' + str(i)} | {f'[{name}]({url})' if url else name} |")
            terms = " ".join(_cell(x) for x in (lic.get("kind"), lic.get("version")) if x) or _cell(d.get("license"))
            if i == 1:
                out.append(f"| 이용조건 | {terms or '확인 전'} |")
            if s.get("attribution") or d.get("attribution"):
                out.append(f"| 표시 문구{'' if i == 1 else ' ' + str(i)} | {_cell(s.get('attribution') or d.get('attribution'))} |")
            if s.get("info_as_of"):
                out.append(f"| 기준일{'' if i == 1 else ' ' + str(i)} | {_cell(s.get('info_as_of'))} |")
    else:
        out.append("JSON에 원천(sources)이 없습니다. 창작 대본이면 그대로 두고, 원천이 있으면 원천 칸을 채워 다시 올립니다.")
    return cid.lower(), "\n".join(out) + "\n"


async def put_json(u, filename, raw):
    """대본 JSON 파일 하나(한 편 객체 또는 여러 편 배열) → md로 바꿔 put_md. 원본 JSON도 scripts_live/json/에 남긴다."""
    data = json.loads(raw)
    res = []
    items = data if isinstance(data, list) else [data]
    hit = match_by_name(filename) if len(items) == 1 else None  # 한 편짜리 파일은 파일 이름으로 기존 대본을 찾는다
    for d in items:
        label = str(d.get("content_id") or filename) if isinstance(d, dict) else filename
        note = ""
        if hit and isinstance(d, dict) and str(d.get("content_id") or "").strip().lower() != hit:
            note = f" · 파일 안 content_id {d.get('content_id') or '(없음)'} 대신 파일 이름 기준 {hit.upper()}로 넣음"
            d = {**d, "content_id": hit}
        try:
            cid, md = json_to_md(d)
        except ValueError as e:
            res.append({"file": filename, "ok": False, "msg": f"{label}: {e}"})
            continue
        code, r = await put_md(u, f"{cid}_{d.get('title', '')}.md", md, upload_name=filename if len(items) == 1 else None)
        if code == 200:
            (SCRIPTS_LIVE / "json").mkdir(exist_ok=True)
            (SCRIPTS_LIVE / "json" / f"{SAFE.sub('_', cid)}.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            extra = " · 생성기 상태 hold" if d.get("status") == "hold" else ""
            res.append({"file": filename, "ok": True, "msg": f"{r['id']} {'새 대본' if r.get('new') else '덮어씀'} (JSON)" + extra +
                        (f" · {r['status']}" if r.get("status") else "") + note +
                        (f" · 숫자·영문 {len(r['warnings'])}줄 확인" if r.get("warnings") else "")})
        else:
            res.append({"file": filename, "ok": False, "msg": f"{label}: {r['detail']}"})
    return res


async def api_upload_files(request):
    """파일 여러 개 한 번에 올리기: 대본 .json·.md, 결과 .csv(대본 검수·목소리 배정), 또는 그것들을 묶은 .zip"""
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    files = (await request.json()).get("files", [])
    out = []
    for f in files[:200]:
        try:
            await put_file(u, str(f.get("name", "")), base64.b64decode(f.get("b64", "")), out)
        except Exception as e:
            out.append({"file": f.get("name"), "ok": False, "msg": str(e)[:200]})
    kick_auto()
    return JSONResponse({"ok": True, "results": out})


async def api_upload(request):
    """스크립트 끝에서 한 줄로 올리기 (자동화용). 화면 업로드와 같은 처리.
    curl -u 이름:비밀번호 --data-binary @파일 "https://studio.onaircar.com/admin/api/upload?name=파일이름.csv" """
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    name = request.query_params.get("name", "")
    out = []
    try:
        await put_file(u, name, await request.body(), out)
    except Exception as e:
        out.append({"file": name, "ok": False, "msg": str(e)[:200]})
    kick_auto()
    return JSONResponse({"ok": bool(out) and all(r.get("ok") for r in out), "results": out})


async def put_file(u, name, data, out):
    """파일 하나 처리: 대본 .json·.md, 결과 .csv(대본 검수·목소리 배정), 또는 그것들을 묶은 .zip. 결과는 out에 쌓는다"""
    if True:
        low = name.lower()
        if low.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for n in z.namelist():
                    if not n.endswith("/") and not n.split("/")[-1].startswith((".", "__")):
                        await put_file(u, n.split("/")[-1], z.read(n), out)
            return
        if low.endswith(".md"):
            code, r = await put_md(u, name, data.decode("utf-8-sig"), upload_name=name)
            out.append({"file": name, "ok": code == 200, "msg": (f"{r['id']} {'새 대본' if r.get('new') else '덮어씀'}" +
                        (f" · {r['status']}" if r.get("status") else "") +
                        (f" · 숫자·영문 {len(r['warnings'])}줄 확인" if r.get("warnings") else "")) if code == 200 else r["detail"]})
            return
        if low.endswith(".json"):
            out.extend(await put_json(u, name, data.decode("utf-8-sig")))
            return
        if low.endswith(".csv"):
            out.extend(apply_csv(u, name, data.decode("utf-8-sig")))
            return
        out.append({"file": name, "ok": False, "msg": "json, md, csv, zip만 받습니다"})


def apply_csv(u, name, text):
    """결과 CSV 반영. 열 이름으로 종류를 가린다: 판정 → 대본 검수, voice → 목소리 배정"""
    rdr = list(csv.DictReader(io.StringIO(text)))
    if not rdr:
        return [{"file": name, "ok": False, "msg": "빈 CSV"}]
    cols = {c.strip() for c in rdr[0].keys() if c}
    vnames = {v["name"] for v in rows("voices")}
    st = pipe_states()
    out = []
    for r in rdr:
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        cid = r.get("id", "").lower()
        got = CON.execute("select data from items where tbl='scripts' and id=?", (cid,)).fetchone()
        if not got:
            out.append({"file": name, "ok": False, "msg": f"{cid or '(id 없음)'}: 없는 대본"})
            continue
        row = json.loads(got[0])
        changes = {}
        if "판정" in cols:
            v = r.get("판정", "")
            if v not in ("통과", "수정 필요"):
                out.append({"file": name, "ok": False, "msg": f"{cid}: 판정은 통과 또는 수정 필요"})
                continue
            if stage(row, st) != 2:
                out.append({"file": name, "ok": False, "msg": f"{cid}: 제출된 대본이 아니라 건너뜀"})
                continue
            changes = {"refine_status": v, "refine_memo": r.get("사유", "")}
            if v == "수정 필요":
                changes["script_status"] = "수정 중"
        elif "voice" in cols:
            v, v2 = r.get("voice", ""), r.get("pick2", "")
            if v not in vnames or (v2 and v2 not in vnames):
                out.append({"file": name, "ok": False, "msg": f"{cid}: 라인업에 없는 목소리 {v} {v2}"})
                continue
            changes = {"voice": v, "pick2": v2, "voice_reason": r.get("reason", ""), "voice_status": "배정"}
        else:
            return [{"file": name, "ok": False, "msg": "열 이름을 모르겠습니다 (결과양식.csv 그대로 써 주세요)"}]
        now = time.time()
        with lock:
            for k, v in changes.items():
                if str(row.get(k, "")) != v:
                    CON.execute("insert into log values (?,?,?,?,?,?,?)", (now, u["id"], "scripts", cid, k, str(row.get(k, "")), v))
            if "voice" in changes and changes["voice"] != row.get("voice") and st.get(cid, {}).get("audio_version"):
                row["audio_stale"] = "목소리가 바뀜 · 음원 다시 만들어야 함"
            row.update(changes)
            if "voice" in changes:
                row["voice_id"] = {v["name"]: v["voice_id"] for v in rows("voices")}[changes["voice"]]
            CON.execute("update items set data=?, updated_by=?, updated_at=? where tbl='scripts' and id=?",
                        (json.dumps(row, ensure_ascii=False), u["id"], now, cid))
            CON.commit()
        out.append({"file": name, "ok": True, "msg": f"{cid}: " + ", ".join(f"{k}={v}" for k, v in changes.items() if v)[:120]})
    return out


KITS = HERE / "admin_static" / "kits"


def csv_text(data, keys):
    buf = io.StringIO()
    w = csv.DictWriter(buf, keys, extrasaction="ignore")
    w.writeheader()
    w.writerows(data)
    return "\ufeff" + buf.getvalue()


async def api_prompt(request):
    if not who(request):
        return Response("login required", 401)
    f = KITS / f"{request.path_params['kind']}_PROMPT.md"
    if f.parent != KITS or not f.exists():
        return Response("unknown", 404)
    return Response(f.read_text(), media_type="text/plain; charset=utf-8", headers={"cache-control": "private, no-store"})


async def api_kit(request):
    """단계별 작업 꾸러미 zip: 내 AI에 줄 프롬프트 + 그 일에 필요한 CSV + 결과 양식"""
    if not who(request):
        return Response("login required", 401)
    kind = request.path_params["kind"]
    st = pipe_states()
    sc = rows("scripts")
    files = {}
    if kind == "write":
        files["PROMPT.md"] = (KITS / "write_PROMPT.md").read_text()
        files["대본양식.md"] = (HERE / "admin_static" / "template.md").read_text()
        files["원천목록.csv"] = csv_text(rows("sources"), ["id", "name", "grade", "credit", "url", "terms", "types"])
        files["기존대본.csv"] = csv_text(sc, ["id", "title", "topic_code", "topic_name", "format"])
        sys.path.insert(0, str(REPO / "server"))
        from teum.catalog.categories import CODES, GROUPS
        files["카테고리.csv"] = csv_text([{"code": c, "name": n, "group": next(g for g, cs in GROUPS.items() if c in cs)} for c, n in CODES.items()], ["code", "name", "group"])
        if (SCRIPTS_LIVE / "02_이사_첫날_상자_하나.md").exists():
            files["예시대본.md"] = (SCRIPTS_LIVE / "02_이사_첫날_상자_하나.md").read_text()
    elif kind == "review":
        files["PROMPT.md"] = (KITS / "review_PROMPT.md").read_text()
        files["검수대상.csv"] = csv_text([s for s in sc if stage(s, st) == 2], ["id", "title", "format", "body", "source_note"])
        files["원천목록.csv"] = csv_text(rows("sources"), ["id", "name", "grade", "credit", "url"])
        files["결과양식.csv"] = "\ufeffid,판정,사유\n"
        # 정제 파이프라인의 원문 대조·사실 대조에 쓰도록 생성기 JSON 원본을 그대로 넣는다
        for s in sc:
            if stage(s, st) == 2:
                j = SCRIPTS_LIVE / "json" / f"{SAFE.sub('_', s['id'])}.json"
                if j.exists():
                    files[f"json/{j.name}"] = j.read_text(encoding="utf-8")
    elif kind == "voice":
        files["PROMPT.md"] = (KITS / "voice_PROMPT.md").read_text()
        files["배정대상.csv"] = csv_text([s for s in sc if stage(s, st) in (2, 3)], ["id", "title", "topic_name", "format", "voice", "body"])
        vs = rows("voices")
        for v in vs:
            v["sample"] = PREVIEW.get(v["voice_id"], "")
        files["목소리.csv"] = csv_text(vs, ["name", "role", "tc_category", "rank", "sample"])
        ab = [dict(zip(("ts", "user", "script_id", "a", "b", "choice", "why"), r)) for r in CON.execute("select * from ab order by ts")]
        files["지난판정.csv"] = csv_text(ab, ["script_id", "a", "b", "choice", "why"])
        files["결과양식.csv"] = "\ufeffid,voice,pick2,reason\n"
    else:
        return Response("unknown", 404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, t in files.items():
            z.writestr(n, t)
    label = {"write": "대본쓰기", "review": "대본검수", "voice": "목소리배정"}[kind]
    from urllib.parse import quote
    fn = f"teum_{label}_{time.strftime('%m%d_%H%M')}.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"cache-control": "private, no-store", "content-disposition": f"attachment; filename*=UTF-8''{quote(fn)}"})


def feedback_name(s):
    """검수 의견 파일의 키: 올렸던 파일 이름 → 원본 JSON의 content_id.json → 사이트 md 이름"""
    if s.get("upload_name"):
        return s["upload_name"]
    j = SCRIPTS_LIVE / "json" / f"{SAFE.sub('_', s['id'])}.json"
    if j.exists():
        try:
            return f"{json.loads(j.read_text(encoding='utf-8')).get('content_id') or s['id']}.json"
        except ValueError:
            pass
    return s.get("script_file") or f"{s['id']}.json"


async def api_feedback(request):
    """'수정 필요' 대본들의 {파일 이름: 검수 의견} JSON 한 파일. 대본과 같이 AI에 주고, 고친 파일을 같은 이름으로 다시 올린다"""
    if not who(request):
        return Response("login required", 401)
    out = {}
    for s in rows("scripts"):
        if s.get("refine_status") != "수정 필요":
            continue
        with lock:
            since = CON.execute("select max(ts) from log where tbl='scripts' and id=? and field='refine_status' and new='수정 필요'",
                                (s["id"],)).fetchone()[0] or 0
            talk = CON.execute("select user, new from log where tbl='scripts' and id=? and field='의견' and ts>=? order by ts",
                               (s["id"], since)).fetchall()
        notes = [str(s.get("refine_memo") or "").strip()] + [f"({who_}) {t}" for who_, t in talk]
        out[feedback_name(s)] = "\n".join(x for x in notes if x) or "(검수 의견 없음)"
    from urllib.parse import quote
    fn = f"teum_검수의견_{time.strftime('%m%d_%H%M')}.json"
    return Response(json.dumps(out, ensure_ascii=False, indent=2), media_type="application/json; charset=utf-8",
                    headers={"cache-control": "private, no-store", "content-disposition": f"attachment; filename*=UTF-8''{quote(fn)}"})


async def guide(request):
    if not who(request):
        return RedirectResponse("/admin/login", 303)
    return FileResponse(HERE / "admin_static" / "guide.html", headers={"cache-control": "private, no-store"})


async def api_template(request):
    if not who(request):
        return Response("login required", 401)
    return FileResponse(HERE / "admin_static" / "template.md", media_type="text/markdown; charset=utf-8")


def synth_one(cid, uid):
    """배정된 목소리로 Typecast 합성(동기). 성공하면 mp3 경로와 목소리"""
    row = json.loads(CON.execute("select data from items where tbl='scripts' and id=?", (cid,)).fetchone()[0])
    vid = {v["name"]: v["voice_id"] for v in rows("voices")}.get(row.get("voice", ""))
    if not vid:
        raise ValueError("목소리 미배정")
    text = "\n".join(l.strip() for l in (row.get("body") or "").splitlines() if l.strip())
    if credits()["today_used"] + len(text) > DAILY_CREDITS:
        raise ValueError(f"오늘 한도 {DAILY_CREDITS:,} 크레딧 초과")
    tc = tc_mod()
    req = {"voice_id": vid, "text": text, "model": tc.MODEL, "language": "KOR", "prompt": {"emotion_type": "smart"},
           "output": {"audio_format": "mp3", "audio_tempo": 1.0, "target_lufs": -16}}
    audio = tc.call("POST", "/v1/text-to-speech", req, soft=True)
    if audio is None:
        req["prompt"] = {"emotion_type": "preset", "emotion_preset": "normal"}
        audio = tc.call("POST", "/v1/text-to-speech", req)
    AUDIO.mkdir(exist_ok=True)
    f = AUDIO / f"{cid}__{vid}.mp3"
    f.write_bytes(audio)
    with lock:
        now = time.time()
        CON.execute("insert into synth values (?,?,?,?,?)", (now, uid, cid, row["voice"], len(text)))
        row.pop("audio_stale", None)
        row.pop("stt", None)
        CON.execute("update items set data=? where tbl='scripts' and id=?", (json.dumps(row, ensure_ascii=False), cid))
        CON.execute("insert into log values (?,?,?,?,?,?,?)", (now, uid, "scripts", cid, "음성 만들기", "", f"{row['voice']} · {len(text)}자"))
        CON.commit()
    return f, vid


async def run_jobs(uid):
    JOBS.update(running=True, done=[], failed=[], by=uid, started=time.time())
    while JOBS["queue"]:
        cid = JOBS["queue"].pop(0)
        try:
            f, vid = await asyncio.to_thread(synth_one, cid, uid)
            res = await ops("attach", cid, f, vid)
            if not res.get("ok"):
                raise ValueError(res.get("error"))
            JOBS["done"].append(cid)
        except Exception as e:
            JOBS["failed"].append(f"{cid}: {str(e)[:120]}")
            AUTO["fail"][cid] = time.time()  # 자동 합성은 실패한 편을 하루 동안 다시 시도하지 않는다(크레딧 보호)
    JOBS["running"] = False
    await asyncio.to_thread(refresh_credit)
    if JOBS["done"]:
        asyncio.create_task(stt_check(list(JOBS["done"])))


def body_text(s):
    return "\n".join(l.strip() for l in (s.get("body") or "").splitlines() if l.strip())


def hold_src(s):
    """출처 확인이 남은 대본(인스타그램 각색)은 자동 합성하지 않는다. 버튼으로는 만들 수 있다"""
    return "instagram" in (s.get("source_note") or "").lower()


def auto_assign():
    st = pipe_states()
    vid = {v["name"]: v["voice_id"] for v in rows("voices")}
    n = 0
    with lock:
        now = time.time()
        for s in rows("scripts"):
            v = AUTO_VOICE.get(s.get("topic_code", ""))
            if stage(s, st) != 3 or s.get("voice") or hold_src(s) or v not in vid:
                continue
            ch = {"voice": v, "voice_status": "배정", "voice_reason": "주제 기본 목소리 (자동 배정)"}
            for k, x in ch.items():
                CON.execute("insert into log values (?,?,?,?,?,?,?)", (now, "auto", "scripts", s["id"], k, str(s.get(k, "")), x))
            s.update(ch)
            s["voice_id"] = vid[v]
            CON.execute("update items set data=?, updated_by=?, updated_at=? where tbl='scripts' and id=?",
                        (json.dumps(s, ensure_ascii=False), "auto", now, s["id"]))
            n += 1
        CON.commit()
    return n


async def auto_tick():
    """3단계(검수 통과) 대본에 목소리를 붙이고 오늘 한도 안에서 합성을 돌린다"""
    if not AUTO_ON or JOBS["running"] or AUTO["ticking"]:
        return
    AUTO["ticking"] = True
    try:
        AUTO["last"] = time.time()
        await asyncio.to_thread(auto_assign)
        if JOBS["running"]:
            return
        st = pipe_states()
        left = DAILY_CREDITS - credits()["today_used"]
        q = []
        for s in rows("scripts"):
            if stage(s, st) != 3 or not s.get("voice") or hold_src(s) or time.time() - AUTO["fail"].get(s["id"], 0) < 86400:
                continue
            n = len(body_text(s))
            if n <= left:
                q.append(s["id"])
                left -= n
        if q:
            JOBS["queue"] = q
            await run_jobs("auto")
    except Exception as e:
        AUTO["error"] = str(e)[:200]
    finally:
        AUTO["ticking"] = False


async def auto_loop():
    while True:
        await auto_tick()
        await asyncio.sleep(600)


def ensure_auto():
    if AUTO_ON and not AUTO["started"]:
        AUTO["started"] = True
        asyncio.get_running_loop().create_task(auto_loop())


def kick_auto():
    """검수 결과가 들어오면 10분을 기다리지 않고 바로 한 번 돈다"""
    ensure_auto()
    if AUTO_ON:
        asyncio.get_running_loop().create_task(auto_tick())


async def stt_check(ids):
    """합성된 음원을 Whisper로 받아 적어 대본과 대조(teum_stt.py, GPU 0). 결과는 대본 행의 stt 칸"""
    if not _STT_LOCK:
        _STT_LOCK.append(asyncio.Lock())
    vid = {v["name"]: v["voice_id"] for v in rows("voices")}
    items = []
    for s in rows("scripts"):
        f = AUDIO / f"{s['id']}__{vid.get(s.get('voice', ''), '')}.mp3"
        if s["id"] in ids and f.exists():
            items.append({"id": s["id"], "mp3": str(f), "text": body_text(s)})
    if not items:
        return
    async with _STT_LOCK[0]:
        p = await asyncio.create_subprocess_exec(*STT_PY, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.DEVNULL)
        out, _ = await p.communicate(json.dumps(items, ensure_ascii=False).encode())
    for line in out.decode(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        with lock:
            got = CON.execute("select data from items where tbl='scripts' and id=?", (d.get("id"),)).fetchone()
            if not got:
                continue
            row = json.loads(got[0])
            if "error" in d:
                row["stt"] = {"error": d["error"], "ts": time.time()}
                note = "받아쓰기 실패"
            else:
                flag = d["cer"] > STT_LIMIT
                row["stt"] = {"cer": d["cer"], "diffs": d["diffs"], "flag": flag, "ts": time.time()}
                note = f"불일치 {d['cer'] * 100:.1f}%" + (" · 확인 필요" if flag else "")
            CON.execute("update items set data=? where tbl='scripts' and id=?", (json.dumps(row, ensure_ascii=False), d["id"]))
            CON.execute("insert into log values (?,?,?,?,?,?,?)", (time.time(), "auto", "scripts", d["id"], "받아쓰기 대조", "", note))
            CON.commit()


async def api_synth(request):
    """검수 통과 대본의 음성 만들기(관리자 실행). ids가 없으면 3단계(음성 대기) 전부"""
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    if "admin" not in u.get("roles", []):
        return JSONResponse({"detail": "음성 만들기는 관리자만 실행합니다"}, 403)
    if JOBS["running"]:
        return JSONResponse({"detail": "이미 만드는 중입니다"}, 409)
    st = pipe_states()
    ready = [s for s in rows("scripts") if stage(s, st) == 3]
    ids = (await request.json()).get("ids") or [s["id"] for s in ready]
    ok_ids = {s["id"] for s in ready if s.get("voice")}
    bad = [i for i in ids if i not in ok_ids]
    if bad:
        return JSONResponse({"detail": "검수 통과·목소리 배정이 안 된 대본: " + ", ".join(bad)}, 400)
    need_ = sum(len((s.get("body") or "").replace("\n", "")) for s in ready if s["id"] in ids)
    if credits()["today_used"] + need_ > DAILY_CREDITS:
        return JSONResponse({"detail": f"오늘 한도를 넘습니다: 필요 {need_:,}, 오늘 쓴 양 {credits()['today_used']:,}, 한도 {DAILY_CREDITS:,}"}, 429)
    JOBS["queue"] = list(ids)
    asyncio.create_task(run_jobs(u["id"]))
    return JSONResponse({"ok": True, "queued": len(ids), "credits": need_})


def release_status(sid):
    """생성기 JSON 원본의 release_status (pending_content_review, pending_processing_review,
    pending_editorial_approval, blocked). 값이 있으면 공개 전 검토가 끝나지 않은 대본이라 앱에 게시하지 않는다"""
    j = SCRIPTS_LIVE / "json" / f"{SAFE.sub('_', sid)}.json"
    try:
        return json.loads(j.read_text(encoding="utf-8")).get("release_status") or ""
    except (OSError, ValueError, AttributeError):
        return ""


async def api_add(request):
    """음성 완료(4단계) 대본을 듣고 앱에 추가: 파이프라인 승인 → 게시 → 팀 API 다시 읽기"""
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    st = pipe_states()
    four = {s["id"] for s in rows("scripts") if stage(s, st) == 4}
    ids = (await request.json()).get("ids") or sorted(four)
    ids = [i for i in ids if i in four]
    gated = {i: release_status(i) for i in ids if release_status(i)}
    ids = [i for i in ids if i not in gated]
    if not ids:
        detail = "앱에 추가할 음성 완료 대본이 없습니다"
        if gated:
            detail = "생성기가 공개 전 검토 대기로 표시한 대본이라 게시하지 않습니다: " + ", ".join(f"{k}({v})" for k, v in gated.items())
        return JSONResponse({"detail": detail, "release_hold": gated}, 400)
    for cid in ids:
        if st[cid]["state"] == "tts_done":
            how = "받아쓰기 대조 통과, 사람 청취 없이 자동 게시" if u["id"] == "auto" else "듣고 앱에 추가"
            res = await ops("review", cid, "approve", f"{u['name']}: {how}")
            if not res.get("ok"):
                return JSONResponse({"detail": f"{cid}: {res.get('error')}"}, 400)
    res = await ops("publish")
    if not res.get("ok"):
        return JSONResponse({"detail": res.get("error")}, 500)
    env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
    # 게시한 뒤 API가 새 카탈로그를 읽게 다시 띄운다. 한 서버 실행기(infra/one-server/teum_up.py)가 TEUM_API_RESTART에 자기 명령을 넣어 준다.
    # 값이 없으면 다시 띄우지 않는다(같은 서버의 다른 설치본을 건드리지 않게)
    restart = os.environ.get("TEUM_API_RESTART", "").split()
    restarted = False
    if restart:
        p = await asyncio.create_subprocess_exec(*restart, env=env)
        restarted = await p.wait() == 0
    with lock:
        for cid in ids:
            CON.execute("insert into log values (?,?,?,?,?,?,?)", (time.time(), u["id"], "scripts", cid, "앱에 추가", "", "팀 API 게시"))
        CON.commit()
    return JSONResponse({"ok": True, "added": ids, "api_restart": restarted, "release_hold": gated})


def drop_scripts(u, ids, reason, restore=False):
    """대본 제외(드랍)와 되돌리기. 제외는 1·2단계(쓰는 중·검수 대기)만, 본인 것이나 관리자. 파일·파이프라인은 그대로 둔다"""
    st = pipe_states()
    done, skipped = [], []
    with lock:
        for rid in ids:
            r = CON.execute("select data from items where tbl='scripts' and id=?", (rid,)).fetchone()
            if not r:
                skipped.append(rid); continue
            row = json.loads(r[0])
            owner = row.get("owner")
            mine = owner == u["id"] if owner else "script" in u["roles"]  # 담당이 비어 있는 대본은 대본 담당 역할이 뺄 수 있다
            if "admin" not in u["roles"] and not mine:
                skipped.append(rid); continue
            if restore:
                if row.get("script_status") != "제외":
                    skipped.append(rid); continue
                old, new = "제외", row.pop("drop_prev", "") or "제출"
                for k in ("drop_by", "drop_at", "drop_reason"):
                    row.pop(k, None)
            else:
                if stage(row, st) not in (1, 2):
                    skipped.append(rid); continue
                old, new = row.get("script_status", ""), "제외"
                row.update(drop_prev=old, drop_by=u["id"], drop_at=time.time(), drop_reason=reason)
            row["script_status"] = new
            now = time.time()
            CON.execute("update items set data=?, updated_by=?, updated_at=? where tbl='scripts' and id=?",
                        (json.dumps(row, ensure_ascii=False), u["id"], now, rid))
            CON.execute("insert into log values (?,?,?,?,?,?,?)", (now, u["id"], "scripts", rid, "script_status", old, new + (f" ({reason})" if reason and not restore else "")))
            done.append(rid)
        CON.commit()
    return done, skipped


async def api_drop(request):
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    b = await request.json()
    ids = [str(x) for x in (b.get("ids") or [])][:2000]
    done, skipped = drop_scripts(u, ids, str(b.get("reason", ""))[:300], bool(b.get("restore")))
    return JSONResponse({"ok": True, "done": len(done), "skipped": len(skipped)})


# ---- 자동 생산(factory) 현황과 조작 ----
# 사이트는 파이프라인을 직접 돌리지 않는다. factory/ 아래 파일을 읽어 보여 주고, 버튼은 깃발 파일만 남긴다.
# 루프(teum_factory loop)가 3초마다 깃발을 보고 움직인다
FACTORY = HOME / "factory"
SA_ROOT = HOME / "script_agent"
COLLECTOR_OUT = REPO / "ai" / "issue_collector" / "output"


def _read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _tick_running():
    """앞선 한 바퀴가 아직 잠금을 쥐고 있는지"""
    import fcntl
    f = FACTORY / ".lock"
    if not f.exists():
        return False
    with open(f) as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
    return False


def _collector_latest():
    """수집기의 가장 최근 실행(날짜·실행 ID·상태·흐름별 상태·오류 코드)"""
    folders = [p for p in COLLECTOR_OUT.glob("*/runs/*") if p.is_dir()]
    if not folders:
        return None
    folder = max(folders, key=lambda p: max([p.stat().st_mtime] + [f.stat().st_mtime for f in p.glob("*.json")]))
    m = _read_json(folder / "manifest.json", {}) or {}
    streams = {}
    for name in ("issue", "market"):
        s = (m.get("streams") or {}).get(name) or {}
        errs = sorted((folder / name / "private" / "model-logs").glob("*/error.json"), key=lambda p: p.stat().st_mtime)
        streams[name] = {"status": s.get("status", "진행 중" if not m else ""),
                         "error": (_read_json(errs[-1], {}) or {}).get("code", "") if errs else ""}
    return {"date": folder.parts[-3], "run_id": folder.name, "status": m.get("status", "진행 중"),
            "at": folder.stat().st_mtime, "streams": streams}


def _collect_next(last, interval):
    """다음 수집 시각. ai/factory steps.collect_due와 같은 규칙(배치 재확인 → 매일 TEUM_COLLECT_AT → 마지막 + 간격)"""
    try:
        return float((FACTORY / "collect_retry.txt").read_text().strip())
    except (OSError, ValueError):
        pass
    at = os.environ.get("TEUM_COLLECT_AT", "")
    if not at:
        return last + interval if last else time.time()
    h, m = (int(x) for x in at.split(":"))
    t = time.localtime(last)
    for day in range(0, 400):
        due = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + day, h, m, 0, 0, 0, -1))
        if due > last:
            return due
    return None


def factory_view():
    env = os.environ.get
    loop = _read_json(FACTORY / "loop.json", {}) or {}
    alive = bool(loop.get("pid")) and Path(f"/proc/{loop['pid']}").exists()
    try:
        last_collect = float((FACTORY / "collect_last.txt").read_text().strip())
    except (OSError, ValueError):
        last_collect = 0
    interval = int(env("TEUM_COLLECT_INTERVAL", "86400"))
    runs = []
    try:
        lines = (FACTORY / "log.jsonl").read_text(encoding="utf-8").splitlines()[-40:]
    except OSError:
        lines = []
    for line in reversed(lines):
        r = _read_json_line(line)
        if r is None:
            continue
        c, g, sub = r.get("1_collect") or {}, r.get("2_generate") or {}, r.get("2_submit") or {}
        rev = {"pass": 0, "revise": 0, "human": 0, "errors": 0, "human_why": []}
        for rd in r.get("3_review") or []:
            rf = rd.get("refine") or {}
            for k in ("pass", "revise", "human", "errors"):
                rev[k] += len(rf.get(k) or [])
            rev["human_why"] += [str(x)[:160] for x in (rf.get("human") or []) + ((rd.get("revise") or {}).get("human") or [])]
        pub = r.get("5_publish") or {}
        runs.append({"ts": r.get("ts", ""), "skipped": r.get("skipped", ""), "error": str(r.get("error", ""))[:300],
                     "collect": {k: c.get(k) for k in ("inbox", "collector", "collector_exit", "error", "next_collect_in_sec", "retry_in_sec") if k in c},
                     "generated": g.get("generated", 0), "gen_blocked": g.get("blocked", ""),
                     "submitted": len(sub.get("uploaded") or []), "held": len(sub.get("held") or []), "submit_errors": len(sub.get("errors") or []),
                     "review": rev, "published": len(pub.get("published") or []) if isinstance(pub, dict) else 0,
                     "cost_usd": (r.get("refine_cost") or {}).get("today_usd", 0)})
    inbox = sorted(((p.name, p.stat().st_mtime) for p in (SA_ROOT / "input").glob("*") if p.suffix.lower() in (".md", ".txt")), key=lambda x: -x[1])
    jobs = len(list((SA_ROOT / "processing").glob("*/job.json")))
    st = (_read_json(FACTORY / "state.json", {}) or {}).get("items", {})
    collect_cmd = env("TEUM_COLLECT_CMD", "")
    return {
        "enabled": env("TEUM_FACTORY", "0") == "1", "paused": (FACTORY / "paused").exists(),
        "run_requested": (FACTORY / "run_now").exists(), "running": _tick_running() or bool(loop.get("running") and alive),
        "alive": alive, "loop": {k: loop.get(k) for k in ("last_start", "last_end", "next_at", "interval", "ts")},
        "now": time.time(),
        "config": {"collect": bool(collect_cmd), "collect_interval": interval, "collect_at": env("TEUM_COLLECT_AT", ""),
                   "generator": env("TEUM_GENERATOR") or "gemini (기본)", "reviewer": env("TEUM_REVIEWER") or "openai (기본)",
                   "refine": env("TEUM_REFINE") or "기본", "max_generate": int(env("TEUM_FACTORY_MAX_GENERATE", "5")),
                   "auto_voice": env("TEUM_AUTO_VOICE", "0") == "1", "daily_credits": DAILY_CREDITS,
                   "auto_publish": env("TEUM_AUTO_PUBLISH", "0") not in ("", "0")},
        "collect": {"last": last_collect, "next": _collect_next(last_collect, interval) if collect_cmd else None,
                    "collector": _collector_latest()},
        "inbox": [{"name": n, "at": t} for n, t in inbox[:30]], "inbox_count": len(inbox), "jobs": jobs,
        "items": {"own": sum(1 for v in st.values() if v.get("own")),
                  "human": [{"id": k, "why": str(v.get("stop") or "검수기가 사람 확인으로 판정")[:200]} for k, v in st.items() if v.get("decision") == "human" or v.get("stop")][:30]},
        "runs": runs,
    }


def _read_json_line(line):
    try:
        return json.loads(line)
    except ValueError:
        return None


async def api_factory(request):
    try:
        need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    return JSONResponse(await asyncio.to_thread(factory_view), headers={"cache-control": "no-store"})


FACTORY_ACTIONS = {"run_now": "지금 한 바퀴", "collect_now": "지금 수집부터", "pause": "일시정지", "resume": "다시 시작"}


async def api_factory_action(request):
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    if "admin" not in u["roles"]:
        return JSONResponse({"detail": "운영 담당만 자동 생산을 조작할 수 있습니다"}, 403)
    act = str((await request.json()).get("action", ""))
    if act not in FACTORY_ACTIONS:
        return JSONResponse({"detail": "모르는 조작"}, 400)
    FACTORY.mkdir(parents=True, exist_ok=True)
    if act == "pause":
        (FACTORY / "paused").write_text(str(time.time()))
    elif act == "resume":
        (FACTORY / "paused").unlink(missing_ok=True)
    else:
        if act == "collect_now":
            (FACTORY / "collect_last.txt").unlink(missing_ok=True)  # 다음 바퀴가 수집 주기를 기다리지 않는다
        (FACTORY / "run_now").write_text(str(time.time()))
    with lock:
        CON.execute("insert into log values (?,?,?,?,?,?,?)", (time.time(), u["id"], "factory", "자동 생산", "factory", "", FACTORY_ACTIONS[act]))
        CON.commit()
    return JSONResponse({"ok": True})


# ---- 롱폼(두 사람 대담): ai/longform/pipeline.py가 $TEUM_HOME/longform 아래에 만든 것을 그대로 보여 준다 (11/14) ----

# ---- 제작진 피드백 → 프롬프트 옵티마이저 (11/14) ----
# 담당자가 단계별로 남긴 의견은 매일 TEUM_OPTIMIZE_AT(기본 04:30)에 자동 생산 루프의 옵티마이저(ai/factory/.../feedback.py)가 모아
# 단계별 규칙으로 정리한다. ② 대본 생성·롱폼은 다음 생산부터 프롬프트에 들어가고, ①·③·④는 담당자에게 수정 제안으로 간다
FEEDBACK = HOME / "feedback"
FB_STAGES = {"collect": ["① 수집", False, "수집기 담당"], "generate": ["② 대본 생성", True, ""], "review": ["③ 검수", False, "검수기 담당"],
             "voice": ["④ 음성", False, "음성·목소리 담당"], "longform": ["롱폼 대담", True, ""]}


def _fb_notes_lock():
    import fcntl
    FEEDBACK.mkdir(parents=True, exist_ok=True)
    f = open(FEEDBACK / ".notes.lock", "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def _fb_next(last):
    """옵티마이저가 다음에 도는 시각: 마지막 실행 뒤 처음 오는 TEUM_OPTIMIZE_AT. 이미 지났으면 다음 바퀴(새 의견이 있을 때)"""
    h, m = (int(x) for x in os.environ.get("TEUM_OPTIMIZE_AT", "04:30").split(":"))
    t = time.localtime(last or time.time() - 86400)
    for day in range(0, 30000):
        due = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + day, h, m, 0, 0, 0, -1))
        if due > (last or time.time() - 86400):
            return due
    return None


def feedback_view():
    notes = _read_json(FEEDBACK / "notes.json", []) or []
    st = _read_json(FEEDBACK / "state.json", {}) or {}
    rules = {k: _read_json(FEEDBACK / "rules" / f"{k}.json", {}) or {} for k in FB_STAGES}
    nxt = _fb_next(st.get("last_run", 0))
    return {"stages": {k: {"label": v[0], "auto": v[1], "owner": v[2]} for k, v in FB_STAGES.items()},
            "notes": sorted(notes, key=lambda n: -n.get("at", 0))[:300], "rules": rules,
            "state": {k: st.get(k) for k in ("active_prompt", "next_prompt", "last_run", "last_result", "activated_at")},
            "optimize_at": os.environ.get("TEUM_OPTIMIZE_AT", "04:30"), "next_run": nxt,
            "collect_at": os.environ.get("TEUM_COLLECT_AT", ""), "requested": (FEEDBACK / "optimize_now").exists(),
            "new": sum(1 for n in notes if n.get("status") == "new")}


async def api_notes(request):
    """GET 피드백 화면 / POST {op: add|delete|reopen|skip|optimize_now}. 의견은 누구나, 지우기·되살리기는 쓴 사람이나 운영 담당, 지금 반영은 운영 담당"""
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    if request.method == "GET":
        return JSONResponse(feedback_view())
    b = await request.json()
    op = b.get("op")
    if op == "optimize_now":
        if "admin" not in u["roles"]:
            return JSONResponse({"detail": "운영 담당만 지금 반영할 수 있습니다"}, 403)
        FEEDBACK.mkdir(parents=True, exist_ok=True)
        (FEEDBACK / "optimize_now").write_text(str(time.time()))
        FACTORY.mkdir(parents=True, exist_ok=True)
        (FACTORY / "run_now").write_text(str(time.time()))  # 루프가 10분을 기다리지 않고 돈다
        detail = "지금 반영 요청"
    else:
        lk = _fb_notes_lock()
        try:
            notes = _read_json(FEEDBACK / "notes.json", []) or []
            if op == "add":
                stage, text, target = str(b.get("stage", "")), str(b.get("text", "")).strip()[:1000], str(b.get("target", "")).strip()[:80]
                if stage not in FB_STAGES or not text:
                    return JSONResponse({"detail": "단계와 내용을 확인하세요"}, 400)
                notes.append({"id": hashlib.sha1(f"{time.time()}{u['id']}{text}".encode()).hexdigest()[:10], "stage": stage,
                              "target": target, "text": text, "by": u["id"], "by_name": u.get("name", ""), "at": time.time(), "status": "new"})
                detail = f"{FB_STAGES[stage][0]}: {text[:120]}"
            else:
                n = next((x for x in notes if x.get("id") == b.get("id")), None)
                if not n:
                    return JSONResponse({"detail": "없는 의견"}, 404)
                if n.get("by") != u["id"] and "admin" not in u["roles"]:
                    return JSONResponse({"detail": "쓴 사람이나 운영 담당만 바꿀 수 있습니다"}, 403)
                if op == "delete":
                    if n.get("status") != "new":
                        return JSONResponse({"detail": "이미 반영된 의견은 지우지 않습니다(기록). 반영 결과가 틀렸으면 새 의견을 남겨 주세요"}, 409)
                    notes.remove(n)
                elif op == "reopen":
                    n.update(status="new", reopened_at=time.time())
                else:
                    return JSONResponse({"detail": "모르는 조작"}, 400)
                detail = f"{op}: {n.get('text', '')[:120]}"
            tmp = FEEDBACK / "notes.json.tmp"
            tmp.write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, FEEDBACK / "notes.json")
        finally:
            lk.close()
    with lock:
        CON.execute("insert into log values (?,?,?,?,?,?,?)", (time.time(), u["id"], "feedback", str(b.get("stage") or b.get("id") or ""), "제작진 피드백", "", detail))
        CON.commit()
    return JSONResponse({"ok": True, **feedback_view()})

LONGFORM = HOME / "longform"
LF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")


def lf_stage(pid):
    out = LONGFORM / "out" / pid
    if (out / "episode.mp3").exists():
        return 5, "음성 완료"
    if (out / "script.json").exists():
        return 4, "대본 완료"
    if (LONGFORM / "sources" / f"{pid}.md").exists():
        return 3, "사실 카드 완료"
    if (LONGFORM / "research" / pid / "pages.json").exists():
        return 2, "자료 조사 완료"
    return 1, "기획"


def lf_list():
    ids = sorted({p.stem for p in (LONGFORM / "plans").glob("*.toml")} | {p.name for p in (LONGFORM / "out").glob("*") if (p / "script.json").exists()})
    items = []
    for pid in ids:
        n, label = lf_stage(pid)
        j = _read_json(LONGFORM / "out" / pid / "script.json", {}) or {}
        sc = j.get("script") or {}
        logs = (LONGFORM / "out" / pid / "pipeline.log.jsonl")
        last = _read_json_line(logs.read_text(encoding="utf-8").splitlines()[-1]) if logs.exists() and logs.stat().st_size else None
        items.append({"id": pid, "stage": n, "stage_label": label, "title": sc.get("title", ""), "show": j.get("show") or [],
                      "lines": len(sc.get("lines") or []), "chars": sum(len(x.get("text", "")) for x in sc.get("lines") or []),
                      "issues": len((j.get("check") or {}).get("issues") or []), "audio": (LONGFORM / "out" / pid / "episode.mp3").exists(),
                      "error": (last or {}).get("error", ""),
                      "redo": "running" if (LONGFORM / "requests" / f"{pid}.running").exists() else "queued" if (LONGFORM / "requests" / f"{pid}.json").exists() else "",
                      "prefs_used": len(j.get("prefs_used") or []),
                      "duration_sec": (_read_json(LONGFORM / "out" / pid / "episode.json", {}) or {}).get("duration_sec"),
                      "hook": next((x.get("text", "") for x in sc.get("lines") or [] if x.get("speaker") == "B"), ""), "updated": max([p.stat().st_mtime for p in (LONGFORM / "out" / pid).glob("*")] or [0])})
    return items


def lf_detail(pid):
    j = _read_json(LONGFORM / "out" / pid / "script.json", {}) or {}
    src = {"sources": [], "facts": [], "cautions": []}
    f = LONGFORM / "sources" / f"{pid}.md"
    if f.exists():
        sec = None
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("## "):
                sec = {"출처": "sources", "사실 카드": "facts", "주의": "cautions"}.get(line[3:].strip())
            elif sec and line[:1] in "-*" and len(line) > 2:
                src[sec].append(line[1:].strip())
    return {**j, **src, "timing": (_read_json(LONGFORM / "out" / pid / "episode.json", {}) or {}).get("lines", [])}


async def api_longform(request):
    try:
        need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    pid = request.query_params.get("id", "")
    if pid:
        if not LF_ID.match(pid):
            return JSONResponse({"detail": "bad id"}, 400)
        return JSONResponse(await asyncio.to_thread(lf_detail, pid), headers={"cache-control": "no-store"})
    return JSONResponse({"items": await asyncio.to_thread(lf_list)}, headers={"cache-control": "no-store"})


LF_PREFS = LONGFORM / "prefs.json"
LF_KINDS = {"all": "전부", "script": "대본 말투·구성", "title": "제목", "research": "자료 조사"}


def lf_prefs_read():
    try:
        return json.loads(LF_PREFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def lf_prefs_write(rows):
    LF_PREFS.parent.mkdir(parents=True, exist_ok=True)
    tmp = LF_PREFS.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, LF_PREFS)


async def api_longform_prefs(request):
    """제작진 선호: GET 목록 / POST {op: add|toggle|delete, ...}. 누구나 남기고, 끄기·지우기는 쓴 사람이나 운영 담당"""
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    if request.method == "GET":
        return JSONResponse({"prefs": lf_prefs_read(), "kinds": LF_KINDS})
    b = await request.json()
    op = b.get("op")
    with lock:
        rows = lf_prefs_read()
        if op == "add":
            text, kind, show = str(b.get("text", "")).strip()[:500], str(b.get("kind", "all")), str(b.get("show", ""))
            if not text or kind not in LF_KINDS or (show and not LF_ID.match(show)):
                return JSONResponse({"detail": "내용·범위를 확인하세요"}, 400)
            rows.append({"id": hashlib.sha1(f"{time.time()}{u['id']}{text}".encode()).hexdigest()[:10], "text": text, "kind": kind,
                         "show": show, "by": u["id"], "by_name": u.get("name", ""), "at": time.time(), "active": True})
        else:
            r = next((x for x in rows if x["id"] == b.get("id")), None)
            if not r:
                return JSONResponse({"detail": "없는 의견"}, 404)
            if r["by"] != u["id"] and "admin" not in u["roles"]:
                return JSONResponse({"detail": "쓴 사람이나 운영 담당만 바꿀 수 있습니다"}, 403)
            if op == "toggle":
                r["active"] = not r.get("active", True)
            elif op == "delete":
                rows.remove(r)
            else:
                return JSONResponse({"detail": "모르는 조작"}, 400)
        lf_prefs_write(rows)
        CON.execute("insert into log values (?,?,?,?,?,?,?)", (time.time(), u["id"], "longform", str(b.get("show") or b.get("id") or ""), "제작진 의견", "", f"{op}: {str(b.get('text', ''))[:120]}"))
        CON.commit()
    return JSONResponse({"ok": True, "prefs": rows})


async def api_longform_redo(request):
    """의견을 반영해 다시 만들기 요청. 롱폼 작업자(pipeline.py watch)가 받아 처리한다. 운영 담당만(모델·음성 비용이 나간다)"""
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    if "admin" not in u["roles"]:
        return JSONResponse({"detail": "운영 담당만 다시 만들 수 있습니다"}, 403)
    b = await request.json()
    pid, frm = str(b.get("id", "")), str(b.get("from", "script"))
    if not LF_ID.match(pid) or frm not in ("script", "title", "voice") or not (LONGFORM / "plans" / f"{pid}.toml").exists():
        return JSONResponse({"detail": "회차나 단계를 확인하세요"}, 400)
    req = LONGFORM / "requests"
    req.mkdir(parents=True, exist_ok=True)
    if (req / f"{pid}.json").exists() or (req / f"{pid}.running").exists():
        return JSONResponse({"detail": "이미 다시 만드는 중입니다"}, 409)
    (req / f"{pid}.json").write_text(json.dumps({"from": frm, "by": u["id"], "at": time.time()}))
    with lock:
        CON.execute("insert into log values (?,?,?,?,?,?,?)", (time.time(), u["id"], "longform", pid, "롱폼 다시 만들기", "", frm))
        CON.commit()
    return JSONResponse({"ok": True})


async def longform_audio(request):
    if not who(request):
        return Response(status_code=401)
    pid = request.path_params["pid"]
    f = LONGFORM / "out" / pid / "episode.mp3"
    if not LF_ID.match(pid) or not f.is_file():
        return Response(status_code=404)
    return FileResponse(f, media_type="audio/mpeg")


# ---- 모델·프롬프트: 숏폼(자동 생산)·롱폼 단계마다 지금 쓰는 모델과 실제 프롬프트 (11/14) ----
# 설정 파일·환경변수·코드를 그 자리에서 읽어 보여 준다(읽기 전용). 프롬프트는 아래 목록에 있는 것만 연다
AI = REPO / "ai"


def _toml(p):
    try:
        import tomllib
        return tomllib.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _consts(path, names):
    """파이썬 파일에서 문자열·사전 상수를 실행하지 않고 꺼낸다(WRITE + "..." 같은 이어 붙이기도)"""
    import ast
    tree, out = ast.parse(Path(path).read_text(encoding="utf-8")), {}

    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return n.value
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            a, b = ev(n.left), ev(n.right)
            return a + b if isinstance(a, str) and isinstance(b, str) else None
        if isinstance(n, ast.Name):
            return out.get(n.id)
        if isinstance(n, ast.Dict):
            try:
                return json.dumps(ast.literal_eval(n), ensure_ascii=False, indent=1)
            except ValueError:
                return None
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            v = ev(node.value)
            if v is not None:
                out[node.targets[0].id] = v
    return {k: out[k] for k in names if k in out}


def _slot(env_name, default):
    """teum.env의 'provider:model' 덮어쓰기. 비었으면 저장소 기본"""
    v = os.environ.get(env_name, "").strip()
    if v and ":" in v:
        p, m = v.split(":", 1)
        return {"provider": p, "model": m, "where": f"teum.env {env_name}"}
    return {**default, "where": default.get("where", "") + " (teum.env에서 비워 둔 기본값)"}


def prompt_registry():
    """id → (이름, 파일 경로 또는 None, 상수 이름). 화면에서 여는 프롬프트는 이것뿐"""
    sa = _toml(AI / "script_agent" / "config.toml")
    ver = (sa.get("pipeline") or {}).get("prompt_version", "")
    reg = {}
    for n in ("select", "extract", "verify", "rights_review"):
        reg[f"ic-{n}"] = (f"수집 · {n}", AI / "issue_collector" / "prompts" / f"{n}.txt", None)
    for n in ("common.txt", "analyze.txt", "generate.txt", "review.txt", "revise.txt", "types.json", "README.md"):
        reg[f"sa-{n.split('.')[0]}"] = (f"생성 · {n}", AI / "script_agent" / "prompts" / ver / n, None)
    reg["rf-prompts"] = ("검수 · prompts.py (실제 문구)", AI / "refine" / "src" / "teum_refine" / "prompts.py", None)
    reg["rf-doc"] = ("검수 · PROMPTS.md (설명서)", AI / "refine" / "PROMPTS.md", None)
    for n, label in (("QUERIES", "조사 · 검색어 만들기"), ("CARDS", "카드 · 사실 카드 뽑기"), ("VERIFY", "카드 · 카드 대조 검토")):
        reg[f"lf-{n.lower()}"] = (label, AI / "longform" / "pipeline.py", n)
    for n, label in (("WRITE", "대본 · 작성"), ("CHECK", "대본 · 사실 검토"), ("REVISE", "대본 · 수정"),
                     ("TITLE", "제목 · 고르기"), ("TITLE_DEFAULT", "제목 · 기본 규칙"), ("TITLE_STYLE", "제목 · 회차별 규칙")):
        reg[f"lf-{n.lower()}"] = (label, AI / "longform" / "longform.py", n)
    return reg


def pipelines_view():
    ic, sa = _toml(AI / "issue_collector" / "config.toml"), _toml(AI / "script_agent" / "config.toml")
    icm, sam = ic.get("models") or {}, sa.get("models") or {}
    m = lambda d, where: {"provider": d.get("provider", ""), "model": d.get("model", ""), "effort": d.get("effort", ""), "where": where}  # noqa: E731
    sa_mode = os.environ.get("TEUM_SCRIPT_AGENT_MODE") or (sa.get("pipeline") or {}).get("mode", "")
    refine = os.environ.get("TEUM_REFINE", "")
    try:
        tc_model = tc_mod().MODEL
    except Exception:
        tc_model = "ssfm-v31"
    short = [
        {"n": 1, "name": "수집", "what": "어제 뉴스 후보를 모아 시사 2 · 시장 3편을 고르고 근거 기사를 확인해 입력함에 넣는다",
         "models": [{"role": "고르기·추출·검색", **m(icm.get("generator", {}), "ai/issue_collector/config.toml [models.generator]")},
                    {"role": "근거 대조", **m(icm.get("verifier", {}), "ai/issue_collector/config.toml [models.verifier]")}],
         "note": f"실행 방식: {(ic.get('collection') or {}).get('mode', '')} · 검색: Gemini 구글 검색 도구",
         "prompts": ["ic-select", "ic-extract", "ic-verify", "ic-rights_review"]},
        {"n": 2, "name": "생성", "what": "소재를 분석해 3분 낭독 대본을 쓰고 스스로 검토·수정한다",
         "models": [{"role": "대본 작성", **_slot("TEUM_GENERATOR", m(sam.get("generator", {}), "ai/script_agent/config.toml [models.generator]"))},
                    {"role": "자체 검토", **_slot("TEUM_REVIEWER", m(sam.get("reviewer", {}), "ai/script_agent/config.toml [models.reviewer]"))}],
         "note": f"프롬프트 판: {(sa.get('pipeline') or {}).get('prompt_version', '')} · 실행 방식: {sa_mode}",
         "prompts": ["sa-common", "sa-analyze", "sa-generate", "sa-review", "sa-revise", "sa-types", "sa-README"]},
        {"n": 3, "name": "검수", "what": "안전·중복·사실·재미를 따져 통과 / 수정 / 사람 확인으로 나눈다",
         "models": [{"role": "검수 판단", "provider": refine.split(":")[0] if ":" in refine else "claude",
                     "model": refine.split(":", 1)[1] if ":" in refine else "claude-haiku-5-5", "effort": "", "where": "teum.env TEUM_REFINE"},
                    {"role": "안전 분류", "provider": "local", "model": "kakaocorp/kanana-safeguard-8b", "effort": "", "where": f"teum.env TEUM_KANANA_URL ({os.environ.get('TEUM_KANANA_URL', '')})"},
                    {"role": "중복 찾기", "provider": "local", "model": "nlpai-lab/KURE-v1", "effort": "", "where": "ai/refine/src/teum_refine/dedup.py"}],
         "note": "", "prompts": ["rf-prompts", "rf-doc"]},
        {"n": 4, "name": "음성", "what": "주제 기본 목소리로 합성하고 받아쓰기로 대본과 대조한다",
         "models": [{"role": "음성 합성", "provider": "typecast", "model": tc_model, "effort": "", "where": "services/studio/typecast_client.py"},
                    {"role": "받아쓰기 대조", "provider": "local", "model": "openai/whisper-large-v3-turbo", "effort": "", "where": "services/studio/teum_stt.py"}],
         "note": f"자동 합성: {'켜짐' if os.environ.get('TEUM_AUTO_VOICE') == '1' else '꺼짐'} · 하루 {DAILY_CREDITS:,}자", "prompts": []},
        {"n": 5, "name": "게시", "what": "사람이 듣고 팀 API에 게시한다", "models": [], "note": "", "prompts": []},
    ]
    long = [
        {"n": 1, "name": "자료 조사", "what": "조사 지시로 검색어를 만들고, 구글 검색으로 출처를 찾아 원문을 받는다",
         "models": [{"role": "검색어·검색", **m(sam.get("generator", {}), "ai/script_agent/config.toml [models.generator]")}], "note": "원문은 공인 주소·robots.txt를 지켜 받는다", "prompts": ["lf-queries"]},
        {"n": 2, "name": "사실 카드", "what": "원문에서 카드를 뽑고, 구절이 원문에 있는지 기계 대조 뒤 다시 검토한다",
         "models": [{"role": "카드 뽑기", **m(sam.get("generator", {}), "같은 생성 모델")}, {"role": "카드 검토", **m(sam.get("reviewer", {}), "같은 검토 모델")}],
         "note": "카드 12장 미만이면 대본으로 넘어가지 않는다", "prompts": ["lf-cards", "lf-verify"]},
        {"n": 3, "name": "대본", "what": "두 사람 대담을 쓰고, 카드와 대조 검토 → 수정을 최대 3바퀴",
         "models": [{"role": "작성·수정", **m(sam.get("generator", {}), "같은 생성 모델")}, {"role": "사실 검토", **m(sam.get("reviewer", {}), "같은 검토 모델")}],
         "note": "실행 방식: 실시간(배치 아님)", "prompts": ["lf-write", "lf-check", "lf-revise"]},
        {"n": 4, "name": "제목", "what": "후보 6개를 내고 하나를 고른다. 기획에 정한 제목이 있으면 그대로",
         "models": [{"role": "제목", **m(sam.get("generator", {}), "같은 생성 모델")}], "note": "", "prompts": ["lf-title", "lf-title_default", "lf-title_style"]},
        {"n": 5, "name": "음성", "what": "대사마다 기획의 A/B 목소리로 합성해 잇는다",
         "models": [{"role": "음성 합성", "provider": "typecast", "model": tc_model, "effort": "", "where": "기획 파일 voice_a · voice_b"}], "note": "", "prompts": []},
    ]
    reg = prompt_registry()
    for st in short + long:
        st["prompts"] = [{"id": p, "label": reg[p][0]} for p in st["prompts"] if p in reg and Path(reg[p][1]).exists()]
    return {"shortform": short, "longform": long, "prefs": [p for p in lf_prefs_read() if p.get("active", True)]}


async def api_pipelines(request):
    try:
        need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    pid = request.query_params.get("prompt", "")
    if pid:
        reg = prompt_registry()
        if pid not in reg:
            return JSONResponse({"detail": "없는 프롬프트"}, 404)
        label, path, const = reg[pid]
        text = _consts(path, [const]).get(const, "") if const else Path(path).read_text(encoding="utf-8")
        return JSONResponse({"label": label, "path": str(Path(path).relative_to(REPO)) + (f" · {const}" if const else ""), "text": text})
    return JSONResponse(await asyncio.to_thread(pipelines_view), headers={"cache-control": "no-store"})


# ---- 음성 보관함: A100에서 옮겨 온 지난 실험·합성 음성 (11/14) ----
# 대본·카탈로그와 연결되지 않은 보관용이다. 색인 CSV(path_in_tar, original_path, run, sha1, bytes)를 읽어 보여 주고,
# 파일은 색인 번호로만 내준다(경로를 받지 않으므로 보관함 밖 파일은 열 수 없다)
ARCHIVE = Path(os.environ.get("TEUM_AUDIO_ARCHIVE", str(HOME.parent / "archive" / "a100_audio")))
_ARCH = {"rows": None, "mtime": 0}


def archive_rows():
    idx = next(iter(sorted(ARCHIVE.glob("runs/*/batch2_index.csv"))), None)
    if not idx:
        return []
    if _ARCH["rows"] is None or idx.stat().st_mtime != _ARCH["mtime"]:
        with open(idx, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        _ARCH.update(rows=[{"i": n, "path": r["path_in_tar"], "run": r["run"], "bytes": int(r.get("bytes") or 0),
                            "name": r["path_in_tar"].rsplit("/", 1)[-1]} for n, r in enumerate(rows)], mtime=idx.stat().st_mtime)
    return _ARCH["rows"]


async def api_archive(request):
    try:
        need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    q = request.query_params
    rows = await asyncio.to_thread(archive_rows)
    runs = {}
    for r in rows:
        x = runs.setdefault(r["run"], {"run": r["run"], "count": 0, "bytes": 0})
        x["count"] += 1
        x["bytes"] += r["bytes"]
    sel = [r for r in rows if (not q.get("run") or r["run"] == q["run"]) and (not q.get("q") or q["q"].lower() in r["path"].lower())]
    off, lim = max(0, int(q.get("offset", 0) or 0)), min(200, max(1, int(q.get("limit", 100) or 100)))
    return JSONResponse({"total": len(rows), "matched": len(sel), "bytes": sum(r["bytes"] for r in rows),
                         "runs": sorted(runs.values(), key=lambda x: x["run"], reverse=True), "items": sel[off:off + lim]})


async def archive_audio(request):
    if not who(request):
        return Response(status_code=401)
    rows = archive_rows()
    try:
        r = rows[int(request.path_params["i"])]
    except (ValueError, IndexError):
        return Response(status_code=404)
    f = (ARCHIVE / r["path"]).resolve()
    if not f.is_relative_to(ARCHIVE.resolve()) or not f.is_file():
        return Response(status_code=404)
    return FileResponse(f, media_type="audio/wav" if f.suffix.lower() == ".wav" else "audio/mpeg")


async def api_review(request):
    try:
        u = need(request)
    except PermissionError:
        return JSONResponse({"detail": "login required"}, 401)
    b = await request.json()
    cid, ok_, note = str(b.get("id")), bool(b.get("approve")), str(b.get("note", ""))[:500]
    res = await ops("review", cid, "approve" if ok_ else "reject", f"{u['name']}: {note}")
    if not res.get("ok"):
        return JSONResponse({"detail": res.get("error")}, 400)
    with lock:
        CON.execute("insert into log values (?,?,?,?,?,?,?)", (time.time(), u["id"], "scripts", cid, "검수", "", ("승인 " if ok_ else "반려 ") + note))
        CON.commit()
    return JSONResponse({"ok": True})


_routes = Starlette(routes=[
    Route("/admin/", page), Route("/admin", lambda r: RedirectResponse("/admin/", 307)),
    Route("/admin/login", login, methods=["GET", "POST"]), Route("/admin/logout", logout),
    Route("/admin/api/data", api_data), Route("/admin/api/update", api_update, methods=["POST"]),
    Route("/admin/api/ab", api_ab, methods=["POST"]),
    Route("/admin/api/synth", api_synth, methods=["POST"]),
    Route("/admin/api/longform", api_longform),
    Route("/admin/api/longform/prefs", api_longform_prefs, methods=["GET", "POST"]), Route("/admin/api/longform/redo", api_longform_redo, methods=["POST"]), Route("/admin/longform/{pid}.mp3", longform_audio),
    Route("/admin/api/pipelines", api_pipelines), Route("/admin/api/notes", api_notes, methods=["GET", "POST"]),
    Route("/admin/api/archive", api_archive), Route("/admin/archive/{i:int}", archive_audio),
    Route("/admin/api/factory", api_factory), Route("/admin/api/factory/action", api_factory_action, methods=["POST"]),
    Route("/admin/api/review", api_review, methods=["POST"]), Route("/admin/api/drop", api_drop, methods=["POST"]), Route("/admin/api/add", api_add, methods=["POST"]),
    Route("/admin/guide", guide), Route("/admin/template.md", api_template), Route("/admin/api/upload_files", api_upload_files, methods=["POST"]), Route("/admin/api/upload", api_upload, methods=["POST"]),
    Route("/admin/kit/{kind}.zip", api_kit), Route("/admin/feedback.json", api_feedback), Route("/admin/prompt/{kind}", api_prompt), Route("/admin/api/comment", api_comment, methods=["POST"]),
    Route("/admin/export/{name}", export), Route("/admin/audio/{name}", audio), Route("/admin/img/{name}", img),
])


async def app(scope, receive, send):
    """요청이 하나라도 오면 자동화 루프를 켠다(teum_gate가 lifespan을 넘기지 않아서, 11/10)"""
    if scope["type"] == "http":
        ensure_auto()
    await _routes(scope, receive, send)
