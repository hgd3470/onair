"""Typecast API 호출 (음성 합성 ④). 관리 사이트(teum_admin.py)가 쓴다.

키는 환경변수 TYPECAST_API_KEY, 없으면 TYPECAST_ENV 파일(기본 $TEUM_HOME/typecast.env)의 TYPECAST_API_KEY=... 줄에서 읽는다.
키와 응답 원문은 화면이나 로그에 찍지 않는다.
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.typecast.ai"
MODEL = "ssfm-v31"
# Typecast 연동 표시(API 페이지 안내): 직접 HTTP 호출은 User-Agent 끝에 붙인다
UA = "teum-studio/1 typecast-integration/1 (source=api-page; generated_by=claude-code)"


def key():
    k = os.environ.get("TYPECAST_API_KEY", "").strip()
    if k:
        return k
    home = Path(os.environ.get("TEUM_HOME", Path(__file__).resolve().parents[2] / "var"))
    env = Path(os.environ.get("TYPECAST_ENV", home / "typecast.env"))
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("TYPECAST_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError("TYPECAST_API_KEY가 없습니다 (환경변수나 typecast.env)")


def call(method, path, body=None, soft=False):
    """soft=True면 400·422(감정 설정을 안 받는 목소리 등)에서 None을 돌려 호출한 쪽이 다른 설정으로 다시 부르게 한다"""
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"X-API-KEY": key(), "Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if soft and e.code in (400, 422):
            return None
        raise RuntimeError(f"Typecast HTTP {e.code} {path}: {e.read()[:300].decode(errors='replace')}") from None


def sub():
    """요금제와 남은 크레딧: {"plan", "credits": {"plan_credits", "used_credits"}, "limits": {...}}"""
    return json.loads(call("GET", "/v1/users/me/subscription"))
