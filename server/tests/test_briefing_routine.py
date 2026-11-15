"""S2-12: 퀵 메뉴 브리핑과 루틴. 외부 API 대신 가짜 응답을 넣어 네트워크 없이 돈다."""
import re
from datetime import datetime, timezone

import pytest

from conftest import API
from test_listening import register
from teum.briefing.korean import read_clock, read_int, read_number, read_temperature
from teum.briefing.service import BriefingService

WEATHER = {"current": {"time": "2026-11-09T08:00", "temperature_2m": 12.4, "weather_code": 0},
           "hourly": {"time": [f"2026-11-09T{h:02d}:00" for h in range(24)],
                      "precipitation_probability": [0] * 15 + [70] + [0] * 8,
                      "weather_code": [0] * 15 + [61] + [0] * 8},
           "daily": {"temperature_2m_max": [16.2], "temperature_2m_min": [6.8]}}
FX = {"rates": {"2026-11-05": {"USD": 1 / 1344.69, "JPY": 100 / 870.0, "EUR": 1 / 1510.0},
                "2026-11-06": {"USD": 1 / 1358.73, "JPY": 100 / 858.0, "EUR": 1 / 1517.0}}}


class FakeFetch:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url):
        self.calls.append(url)
        if self.fail:
            raise OSError("offline")
        return WEATHER if "open-meteo" in url else FX


@pytest.fixture()
def fake(client):
    fetch = FakeFetch()
    client.app.state.briefing = BriefingService(fetch=fetch)
    return fetch


def test_korean_numbers():
    assert read_int(1384) == "천삼백팔십사" and read_int(10000) == "만" and read_int(0) == "영"
    assert read_number(1358.7, 1) == "천삼백오십팔 점 칠"
    assert read_temperature(-3.4) == "영하 삼 도" and read_clock(8, 46) == "오전 여덟 시 사십육 분"


def test_quick_menu_three_cards_without_digits_in_speech(client, fake):
    body = client.post(f"{API}/quick-menu", json={"lat": 37.2411, "lon": 127.0794, "remain_sec": 1920,
                                                  "remain_m": 18400}).json()
    cards = {c["id"]: c for c in body["items"]}
    assert list(cards) == ["weather", "economy", "traffic"]
    assert all(c["available"] and c["tts"] == "device" for c in cards.values())
    for c in cards.values():
        assert not re.search(r"[0-9A-Za-z]", c["text"]), c["text"]   # 낭독 문장은 한글 읽기만
        assert all(len(line) <= 40 for line in c["lines"]), c["lines"]
        assert c["sources"] and c["duration_sec"] > 0
    assert "맑음 12°" in cards["weather"]["subtitle"] and "오후 비 소식" in cards["weather"]["subtitle"]
    assert "오후 세 시쯤 비 소식" in cards["weather"]["text"]
    assert "천삼백오십구 원" in cards["economy"]["text"] and "십사 원 올랐어요" in cards["economy"]["text"]
    assert "삼십이 분" in cards["traffic"]["text"] and "십팔 킬로미터" in cards["traffic"]["text"]
    assert "latitude=37.24&longitude=127.08" in fake.calls[0]   # 위치는 약 1km로 반올림해서만 쓴다


def test_missing_inputs_and_offline(client):
    client.app.state.briefing = BriefingService(fetch=FakeFetch(fail=True))
    cards = {c["id"]: c for c in client.post(f"{API}/quick-menu").json()["items"]}
    assert not cards["weather"]["available"] and "위치" in cards["weather"]["text"]
    assert not cards["traffic"]["available"] and "경로 안내" in cards["traffic"]["text"]
    assert not cards["economy"]["available"]          # 네트워크 실패도 500이 아니라 안내 문장
    assert client.post(f"{API}/briefings/stocks").status_code == 404


def test_cache_and_stale_fallback():
    fetch, clock = FakeFetch(), [1000.0]
    service = BriefingService(fetch=fetch, clock=lambda: clock[0])
    service.build("economy")
    service.build("economy")
    assert len(fetch.calls) == 1                      # 30분 안에는 다시 부르지 않는다
    clock[0] += 3600
    fetch.fail = True
    stale = service.build("economy")
    assert stale["available"] and stale["stale"]      # 실패하면 직전 값


def test_default_routine_and_put_validation(client):
    _, auth = register(client)
    body = client.get(f"{API}/me/routines", headers=auth).json()
    assert body["default"] and body["routines"][0]["name"] == "출근길 루틴"
    assert [s["type"] for s in body["routines"][0]["steps"]] == ["weather", "economy", "traffic", "recommend"]
    bad = {"routines": [{"routine_id": "x", "name": "a", "steps": [{"type": "recommend"}, {"type": "weather"}]}]}
    assert client.put(f"{API}/me/routines", headers=auth, json=bad).status_code == 422
    good = {"routines": [{"routine_id": "home", "name": "퇴근길", "steps": [{"type": "traffic"}, {"type": "recommend"}]}]}
    assert client.put(f"{API}/me/routines", headers=auth, json=good).status_code == 200
    saved = client.get(f"{API}/me/routines", headers=auth).json()
    assert not saved["default"] and saved["routines"][0]["routine_id"] == "home"
    assert client.get(f"{API}/me/routines").status_code == 401


def test_routine_plan_orders_briefings_then_fills_time(client, fake):
    _, auth = register(client)
    plan = client.post(f"{API}/routines/commute/plan", headers=auth,
                       json={"lat": 37.24, "lon": 127.08, "remain_sec": 300, "remain_m": 4000, "hour": 8}).json()
    steps = [i["routine_step"] for i in plan["items"]]
    assert steps[:3] == ["weather", "economy", "traffic"] and set(steps[3:]) == {"recommend"}
    starts = [i["start_at_sec"] for i in plan["items"]]
    assert starts == sorted(starts) and starts[0] == 0
    assert plan["total_sec"] <= plan["budget_sec"] + 1e-6
    assert plan["recommend"]["signal_status"]["vehicle_context"] == "connected"
    stories = [i for i in plan["items"] if i["routine_step"] == "recommend"]
    assert stories and all(s["kind"] in ("story", "ad") for s in stories)


def test_routine_plan_skips_unavailable_and_requires_budget(client, fake):
    _, auth = register(client)
    plan = client.post(f"{API}/routines/commute/plan", headers=auth, json={"minutes": 5}).json()
    assert {s["type"] for s in plan["skipped"]} == {"weather", "traffic"}   # 위치·경로 정보 없음
    assert plan["items"][0]["routine_step"] == "economy"
    assert client.post(f"{API}/routines/commute/plan", headers=auth, json={}).status_code == 422
    assert client.post(f"{API}/routines/nope/plan", headers=auth, json={"minutes": 5}).status_code == 404
