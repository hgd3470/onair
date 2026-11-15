"""공개 주소(studio.onaircar.com, api.onaircar.com)용 앞단: 시범 운용 v2 앱 앞에 비밀번호 로그인을 붙인다.

실행 폴더(runs/…_v2)의 코드는 바꾸지 않고 그대로 불러온다.
- 비밀번호 칸 하나짜리 로그인 페이지(/login). 맞으면 30일짜리 쿠키를 주고, 이후 페이지·API·음원은 쿠키로 통과
  (처음에는 브라우저 기본 로그인 창(Basic 인증)을 썼는데, 앱 안 브라우저에서는 창이 안 떠서 폰에서 못 들어감)
- 점검용으로 Basic 인증(curl -u x:비밀번호)도 받는다
- 같은 접속지(cf-connecting-ip)에서 10분 안에 5번 틀리면 10분 동안 잠김
값: teum.env의 TEUM_PASS(비밀번호), TEUM_COOKIE_KEY(쿠키 서명 키)
"""
import base64
import hashlib
import hmac
import html
import ipaddress
import os
import secrets
import sys
import time
from collections import defaultdict, deque
from urllib.parse import parse_qs

RUN = os.environ.get("TEUM_RUN", "")  # 10/25 시범 플레이어 실행 폴더. 저장소 밖 자료라 값을 줄 때만 붙인다
if RUN and os.path.isdir(RUN):
    sys.path.insert(0, RUN)
    from serving.app import app as inner  # noqa: E402
else:
    async def inner(scope, receive, send):
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
        await send({"type": "http.response.body", "body": b"not found"})
from starlette.staticfiles import StaticFiles  # noqa: E402
from starlette.exceptions import HTTPException
from teum_admin import app as ADMIN, who as admin_who  # noqa: E402  /admin/ 관리 대시보드 (11/7)
import httpx  # noqa: E402
from starlette.requests import Request  # noqa: E402

API_UP = os.environ.get("TEUM_API_UPSTREAM", "http://127.0.0.1:8790")
API_CLIENT = httpx.AsyncClient(base_url=API_UP, timeout=60)
HOP = {b"host", b"authorization", b"cookie", b"connection", b"transfer-encoding", b"content-length"}


async def api_proxy(scope, receive, send):
    """/api/v1/* 를 팀 API 서버로 그대로 넘기고 응답을 흘려보낸다. 인증 머리글·쿠키는 넘기지 않는다"""
    body = b""
    while True:
        m = await receive()
        body += m.get("body", b"")
        if not m.get("more_body"):
            break
    url = scope["path"] + (("?" + scope["query_string"].decode()) if scope.get("query_string") else "")
    headers = [(k, v) for k, v in scope.get("headers", []) if k.lower() not in HOP]
    # 기기 토큰(Bearer)은 팀 API가 사용자를 가리는 데 쓰므로 넘긴다(/events, /history, /me/interests 등). Basic(사이트 로그인)은 넘기지 않는다 (11/10)
    bearer = dict(scope.get("headers") or []).get(b"authorization", b"")
    if bearer[:7].lower() == b"bearer ":
        headers.append((b"authorization", bearer))
    try:
        req = API_CLIENT.build_request(scope["method"], url, headers=headers, content=body)
        r = await API_CLIENT.send(req, stream=True)
    except httpx.HTTPError:
        await send({"type": "http.response.start", "status": 502, "headers": [(b"content-type", b"application/json")]})
        return await send({"type": "http.response.body", "body": b'{"detail":"team api down"}'})
    hs = [(k.encode(), v.encode()) for k, v in r.headers.items() if k.lower() not in ("transfer-encoding", "connection", "cache-control")]  # content-length는 살린다: 아이폰이 길이 없는 음원 응답을 잘 못 튼다(원본 바이트를 그대로 흘려보내 길이가 같다)
    # 음원은 브라우저만 10분 보관(이어 듣기·되감기 때 다시 받지 않게), 나머지는 저장하지 않음. Cloudflare가 저장하지 않게 둘 다 private
    cc = b"private, max-age=600" if scope["path"].startswith(("/api/v1/gen/", "/api/v1/audio/", "/api/v1/ads/")) else b"private, no-store"
    await send({"type": "http.response.start", "status": r.status_code, "headers": hs + [(b"cache-control", cc)]})
    async for chunk in r.aiter_raw():
        await send({"type": "http.response.body", "body": chunk, "more_body": True})
    await r.aclose()
    await send({"type": "http.response.body", "body": b""})

# /tc/ : Typecast 음원 페이지 (runs/20261107-tc_demo/site, 11/7 추가). 로그인 뒤에만 열림
_TC_DIR = os.environ.get("TEUM_TC_DIR", "")
TC = StaticFiles(directory=_TC_DIR, html=True) if _TC_DIR and os.path.isdir(_TC_DIR) else None

# /app/ : 청취 웹앱 (deploy/app_static, 11/10). 팀 API(/api/v1)만 받아 재생한다. 팀 계정 로그인이나 사이트 비밀번호 로그인 뒤에 열림
APP = StaticFiles(directory=os.environ.get("TEUM_APP_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_static")), html=True)
FRONT_APP = os.environ.get("TEUM_FRONT", "app") == "app"  # 첫 화면(/)을 청취 웹앱으로. old면 10/25 시범 플레이어 그대로
PASSWORD = os.environ["TEUM_PASS"]
KEY = os.environ["TEUM_COOKIE_KEY"].encode()
TOKEN = hmac.new(KEY, b"teum-login:" + PASSWORD.encode(), hashlib.sha256).hexdigest()  # 비밀번호가 바뀌면 기존 쿠키도 무효
COOKIE = "teum_auth"
MAX_FAILS, WINDOW, LOCK = 5, 600, 600
fails = defaultdict(deque)
locked_until = {}

LOGIN_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>온에어 시범 플레이어</title>
<style>
:root{{--bg:#0f1720;--panel:#17212c;--ink:#e6edf3;--muted:#93a4b5;--accent:#f59e0b;--line:#2a3846;--bad:#f87171}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);
font:16px/1.5 system-ui,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;padding:16px}}
form{{width:100%;max-width:340px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px;display:grid;gap:12px}}
h1{{font-size:20px;margin:0}}p{{margin:0;color:var(--muted);font-size:14px}}
input{{font:inherit;font-size:20px;letter-spacing:.2em;padding:12px;border-radius:8px;border:1px solid var(--line);background:#0b1218;color:var(--ink);width:100%}}
button{{font:inherit;font-weight:700;padding:12px;border:0;border-radius:8px;background:var(--accent);color:#1b1204}}
.err{{color:var(--bad)}}
</style></head><body>
<form method="post" action="/login">
<h1>온에어 시범 플레이어</h1><p>비밀번호를 입력하세요.</p>{msg}
<input id="pw" name="password" type="password" {inputmode} autocomplete="current-password" autofocus required aria-label="비밀번호">
<button type="submit">들어가기</button>
</form></body></html>"""


def headers_of(scope):
    return dict(scope.get("headers") or [])


def client_ip(scope):
    h = headers_of(scope)
    ip = h.get(b"cf-connecting-ip") or h.get(b"x-forwarded-for", b"").split(b",")[0]
    return ip.decode().strip() or (scope.get("client") or ["?"])[0]


# 차량 앱은 /api/v1에 사이트 로그인 없이 붙는다. 기기 등록(/devices), 추천 목록, 음원은 토큰 없이 부르고,
# 청취 기록처럼 사용자가 걸린 요청에만 기기 토큰(Bearer)을 붙인다. 그래서 Cloudflare를 거치지 않고 이 서버 안이나
# SSH 터널로 들어온 요청은 로그인 없이 API로 넘기고, 토큰 검사는 API가 한다. 공개 주소로 들어온 요청은 그대로 로그인이 필요하다.
# (한 서버 구성에서 8780이 이 입구가 되어 차량 앱이 401을 받던 문제)
API_OPEN_NETS = [ipaddress.ip_network(n.strip(), strict=False)
                 for n in os.environ.get("TEUM_API_OPEN_NETS", "127.0.0.1/32,::1/128").split(",") if n.strip()]
# 11/14: 차량 앱 빌드가 공개 주소의 /api/v1을 로그인 없이 부르도록 연다(팀장 요청). /admin, /app 등 나머지는 그대로 로그인.
# 토큰이 필요한 요청(청취 기록 등)은 여전히 팀 API가 기기 토큰으로 검사한다. TEUM_API_PUBLIC=0이면 예전처럼 공개 주소는 로그인 필요
API_PUBLIC = os.environ.get("TEUM_API_PUBLIC", "1") == "1"


def direct_client(scope):
    """Cloudflare를 거치지 않았고 TEUM_API_OPEN_NETS(기본: 이 서버 자신) 안에서 온 요청이면 True"""
    h = headers_of(scope)
    if b"cf-connecting-ip" in h or b"cf-ray" in h:  # 공개 주소(터널)로 들어온 요청. Cloudflare가 붙이고 손님이 지울 수 없다
        return False
    try:  # uvicorn --proxy-headers가 X-Forwarded-For의 손님 주소를 client로 바꿔 둔다
        ip = ipaddress.ip_address((scope.get("client") or [""])[0])
    except ValueError:
        return False
    return any(ip in n for n in API_OPEN_NETS)


def has_cookie(scope):
    raw = headers_of(scope).get(b"cookie", b"").decode("latin-1")
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and secrets.compare_digest(v, TOKEN):
            return True
    return False


def basic_ok(scope):
    auth = headers_of(scope).get(b"authorization", b"")
    if not auth.startswith(b"Basic "):
        return False
    try:
        _, pw = base64.b64decode(auth[6:]).decode("utf-8").split(":", 1)
    except Exception:
        return False
    return secrets.compare_digest(pw.encode(), PASSWORD.encode())


def record_fail(ip, now):
    q = fails[ip]
    q.append(now)
    while q and q[0] < now - WINDOW:
        q.popleft()
    if len(q) >= MAX_FAILS:
        locked_until[ip] = now + LOCK
        q.clear()


async def send_page(send, status, body, extra=()):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/html; charset=utf-8"), (b"cache-control", b"no-store"), *extra]})
    await send({"type": "http.response.body", "body": body.encode("utf-8")})


def login_page(msg=""):
    numeric = 'inputmode="numeric"' if PASSWORD.isdigit() else ""
    return LOGIN_PAGE.format(msg=f'<p class="err">{html.escape(msg)}</p>' if msg else "", inputmode=numeric)


async def read_body(receive, limit=4096):
    body = b""
    while True:
        m = await receive()
        body += m.get("body", b"")
        if not m.get("more_body") or len(body) > limit:
            return body[:limit]


async def app(scope, receive, send):
    if scope["type"] != "http":
        return await inner(scope, receive, send)
    path, method = scope.get("path", "/"), scope.get("method", "GET")
    ip, now = client_ip(scope), time.time()

    if path == "/admin" or path.startswith("/admin/"):  # 관리 대시보드는 팀원별 계정으로 따로 로그인 (teum_admin.py, 11/7)
        return await ADMIN(scope, receive, send)

    if path.startswith("/api/v1/"):  # 팀 API(server/teum, 127.0.0.1:8790)로 넘긴다. 11/14부터 공개(API_PUBLIC). 끄면 공개 주소는 Basic이나 로그인 쿠키 필요 (11/7)
        if not (API_PUBLIC or direct_client(scope) or admin_who(Request(scope)) or has_cookie(scope) or basic_ok(scope)):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b'Basic realm="teum-api"')]})
            return await send({"type": "http.response.body", "body": b'{"detail":"login required"}'})
        return await api_proxy(scope, receive, send)

    if path == "/app" or path.startswith("/app/") or (FRONT_APP and path == "/" and method == "GET"):
        if path in ("/", "/app"):
            await send({"type": "http.response.start", "status": 307, "headers": [(b"location", b"/app/"), (b"cache-control", b"no-store")]})
            return await send({"type": "http.response.body", "body": b""})
        if not (admin_who(Request(scope)) or has_cookie(scope) or basic_ok(scope)):
            await send({"type": "http.response.start", "status": 303,
                        "headers": [(b"location", b"/admin/login?next=/app/"), (b"cache-control", b"no-store")]})
            return await send({"type": "http.response.body", "body": b""})

        async def send_app(message):  # 로그인 뒤 응답은 Cloudflare가 저장하지 않게 private
            if message["type"] == "http.response.start":
                hs = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                message = {**message, "headers": hs + [(b"cache-control", b"private, no-cache")]}
            await send(message)
        try:
            return await APP({**scope, "path": path[4:], "root_path": scope.get("root_path", "") + "/app"}, receive, send_app)
        except HTTPException as e:  # 없는 파일. StaticFiles를 Starlette 앱 밖에서 직접 불러서 여기서 받는다
            await send({"type": "http.response.start", "status": e.status_code, "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"cache-control", b"no-store")]})
            return await send({"type": "http.response.body", "body": b"not found"})
    if path == "/login":
        if False and locked_until.get(ip, 0) > now:  # 잠금 끔 (팀 결정)
            return await send_page(send, 429, login_page("비밀번호를 여러 번 틀려 10분 동안 잠겼습니다."))
        if method == "POST":
            form = parse_qs((await read_body(receive)).decode("utf-8", "replace"))
            pw = (form.get("password") or [""])[0].strip()
            if secrets.compare_digest(pw.encode(), PASSWORD.encode()):
                cookie = f"{COOKIE}={TOKEN}; Path=/; Max-Age={30 * 86400}; HttpOnly; Secure; SameSite=Lax"
                await send({"type": "http.response.start", "status": 303,
                            "headers": [(b"location", b"/"), (b"set-cookie", cookie.encode()), (b"cache-control", b"no-store")]})
                return await send({"type": "http.response.body", "body": b""})
            record_fail(ip, now)
            return await send_page(send, 401, login_page("비밀번호가 맞지 않습니다."))
        return await send_page(send, 200, login_page())

    if has_cookie(scope) or basic_ok(scope):
        # Cloudflare는 .mp3를 기본으로 캐시해서, 로그인한 사람이 한 번 받은 음원을 로그인 없이도 내줬다(배포 점검에서 확인).
        # 로그인 뒤 응답에는 모두 private를 붙여 Cloudflare가 저장하지 않게 한다. 음원만 브라우저가 10분 보관
        cc = b"private, max-age=600" if path.startswith(("/audio/", "/ads/")) else b"private, no-store"

        async def send_private(message):
            if message["type"] == "http.response.start":
                hs = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                message = {**message, "headers": hs + [(b"cache-control", cc)]}
            await send(message)
        if path in ("/old", "/old/"):  # 10/25 시범 플레이어 첫 화면(예전 /). 그 화면이 부르는 /playlist, /audio 등은 그대로 inner로 간다
            return await inner({**scope, "path": "/"}, receive, send_private)
        if path == "/tc":
            await send({"type": "http.response.start", "status": 307, "headers": [(b"location", b"/tc/")]})
            return await send({"type": "http.response.body", "body": b""})
        if TC and path.startswith("/tc/"):
            return await TC({**scope, "path": path[3:], "root_path": scope.get("root_path", "") + "/tc"}, receive, send_private)
        return await inner(scope, receive, send_private)
    if method == "GET" and path == "/":  # 페이지를 열면 로그인 화면으로
        await send({"type": "http.response.start", "status": 303, "headers": [(b"location", b"/login"), (b"cache-control", b"no-store")]})
        return await send({"type": "http.response.body", "body": b""})
    await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b'{"detail":"login required"}'})
