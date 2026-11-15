"""한 서버 구성에서 차량 앱이 입구(8780)를 거쳐 API에 붙는지 본다.

teum_up.py로 세 프로세스를 임시 폴더에 띄우고, 차량 앱이 부르는 순서(기기 등록 → 목록 → 토큰을 붙인 청취 기록)대로 부른다.
11/14부터 /api/v1은 공개 주소(Cloudflare)로 들어와도 로그인 없이 통과하고 /admin은 로그인이 필요하다.
TEUM_API_PUBLIC=0이면 예전처럼 공개 주소의 /api/v1도 로그인이 필요하다.
"""
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
UP = REPO / "infra/one-server/teum_up.py"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


@pytest.fixture()
def one_server(tmp_path, request):
    web, api = free_port(), free_port()
    env = {**os.environ, "TEUM_HOME": str(tmp_path / "home"), "TEUM_WEB_PORT": str(web), "TEUM_API_PORT": str(api),
           "TEUM_FACTORY": "0", "TEUM_PYTHON": sys.executable,
           "TEUM_API_PUBLIC": getattr(request, "param", "1")}
    subprocess.run([sys.executable, str(UP), "init"], env=env, check=True, capture_output=True)
    log = open(tmp_path / "up.log", "wb")
    proc = subprocess.Popen([sys.executable, str(UP), "up"], env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{web}/api/v1"
        for _ in range(150):
            try:
                if call(base + "/contents")[0] in (200, 401):
                    break
            except OSError:
                pass
            time.sleep(0.2)
        else:
            pytest.fail("한 서버가 뜨지 않음: " + (tmp_path / "up.log").read_text(errors="replace")[-1500:])
        pw = next(l.split("=", 1)[1] for l in (tmp_path / "home/teum.env").read_text().splitlines() if l.startswith("TEUM_PASS="))
        yield base, pw
    finally:
        subprocess.run([sys.executable, str(UP), "down"], env=env, capture_output=True)
        proc.wait(30)


def test_car_app_reaches_api_through_gate(one_server):
    base, _ = one_server
    status, dev = call(base + "/devices", "POST", {"install_id": "test-install", "app_version": "test", "platform": "pleos-connect"})
    assert status == 201, dev
    assert call(base + "/contents")[0] == 200
    assert call(base + "/history", headers={"Authorization": f"Bearer {dev['token']}"})[0] == 200
    assert call(base + "/history", headers={"Authorization": "Bearer not-a-token"})[0] == 401  # 토큰 검사는 API가 한다


VIA_TUNNEL = {"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "203.0.113.9", "cf-ray": "test"}


def test_public_api_open_admin_closed(one_server):
    base, _ = one_server
    assert call(base + "/contents", headers=VIA_TUNNEL)[0] == 200  # 공개 주소에서 로그인 없이 목록
    status, dev = call(base + "/devices", "POST", {"install_id": "pub-install", "app_version": "test", "platform": "pleos-connect"}, headers=VIA_TUNNEL)
    assert status == 201, dev
    assert call(base + "/history", headers={"Authorization": f"Bearer {dev['token']}", **VIA_TUNNEL})[0] == 200
    assert call(base + "/history", headers={"Authorization": "Bearer not-a-token", **VIA_TUNNEL})[0] == 401  # 토큰 검사는 그대로
    root = base.rsplit("/api/v1", 1)[0]
    req = urllib.request.Request(root + "/admin/api/factory", headers=VIA_TUNNEL)
    try:
        urllib.request.urlopen(req, timeout=10)
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    assert code in (401, 403) or code // 100 == 3, code  # /admin은 여전히 로그인


@pytest.mark.parametrize("one_server", ["0"], indirect=True)
def test_public_requests_need_login_when_closed(one_server):
    base, pw = one_server
    via_tunnel = {"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "203.0.113.9"}
    status, body = call(base + "/contents", headers=via_tunnel)
    assert status == 401 and body == {"detail": "login required"}
    assert call(base + "/contents", headers={"x-forwarded-for": "203.0.113.9"})[0] == 401  # 다른 기계에서 넘어온 요청
    assert call(base + "/contents", headers={"Authorization": "Bearer anything", **via_tunnel})[0] == 401  # 아무 Bearer로는 못 연다
    basic = base64.b64encode(f"x:{pw}".encode()).decode()
    assert call(base + "/contents", headers={"Authorization": f"Basic {basic}", **via_tunnel})[0] == 200
