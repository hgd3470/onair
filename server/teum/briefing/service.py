"""퀵 메뉴 브리핑 (S2-12). 짧은 낭독 문장을 만들어 앱에 주고, 앱은 기기 TTS로 읽는다.

- weather: Open-Meteo (키 없음, CC BY 4.0). 현재 날씨·오늘 최고/최저·오늘 남은 시간의 비/눈 소식
- economy: Frankfurter (키 없음, 유럽중앙은행 기준 환율). 달러·엔(100엔)·유로 원화 환율과 전날 대비
- traffic: 앱이 보낸 내비 값(NaviHelper remainTime·remainDistance)으로 남은 거리·소요 시간·도착 시각.
  고속도로 정체 구간은 한국도로공사 API 키가 필요해 아직 넣지 않았다.

외부 호출은 3초 제한이고 결과를 잠깐 저장해 둔다(날씨 10분, 환율 30분). 실패하면 직전 값을 쓰고(stale),
그것도 없으면 available=false로 돌려준다. 위치는 소수 둘째 자리(약 1km)로 반올림해서만 쓰고 저장하지 않는다.
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from teum.briefing.korean import (read_clock, read_int, read_minutes, read_month_day, read_number,
                                  read_temperature)

KST = timezone(timedelta(hours=9))
CHARS_PER_SEC = 4.5          # 대본 생성기와 같은 길이 추정 기준 (공백 뺀 글자 수 / 4.5)
WEATHER_TTL, FX_TTL = 600, 1800
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
FRANKFURTER = "https://api.frankfurter.dev/v1"
TITLES = {"weather": "오늘 날씨", "economy": "경제 브리핑", "traffic": "고속도로 교통상황"}
KINDS = tuple(TITLES)
SOURCES = {
    "weather": {"name": "Open-Meteo", "url": "https://open-meteo.com/", "license": "CC BY 4.0"},
    "economy": {"name": "Frankfurter · 유럽중앙은행 기준 환율", "url": "https://frankfurter.dev/",
                "license": "공개 데이터 (출처 표시)"},
    "traffic": {"name": "차량 내비게이션 (Pleos NaviHelper)", "url": None, "license": None},
}
# WMO 날씨 코드 → (화면 문구, 낭독 문구, 비/눈 여부)
WMO = [((0,), "맑음", "맑고", None), ((1,), "대체로 맑음", "대체로 맑고", None), ((2,), "구름 조금", "구름이 조금 있고", None),
       ((3,), "흐림", "흐리고", None), ((45, 48), "안개", "안개가 끼었고", None),
       ((51, 53, 55, 56, 57), "이슬비", "이슬비가 내리고", "비"), ((61, 63, 65, 66, 67), "비", "비가 내리고", "비"),
       ((71, 73, 75, 77), "눈", "눈이 내리고", "눈"), ((80, 81, 82), "소나기", "소나기가 내리고", "비"),
       ((85, 86), "눈", "눈이 내리고", "눈"), ((95, 96, 99), "뇌우", "천둥 번개가 치고", "비")]


def weather_words(code):
    for codes, label, spoken, wet in WMO:
        if code in codes:
            return label, spoken, wet
    return "알 수 없음", "날씨를 확인하기 어렵고", None


def fetch_json(url, timeout=3.0):
    request = urllib.request.Request(url, headers={"User-Agent": "teum-server/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class BriefingUnavailable(Exception):
    """args[0]: 사용자에게 읽어 줄 문장. 비어 있으면 기본 문장을 쓴다."""


class BriefingService:
    def __init__(self, fetch=fetch_json, clock=time.time):
        self.fetch, self.clock = fetch, clock
        self._cache = {}

    def _cached(self, key, ttl, load):
        now = self.clock()
        hit = self._cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1], False
        try:
            value = load()
        except Exception as exc:  # 네트워크·형식 오류 모두: 직전 값이 있으면 쓴다
            if hit:
                return hit[1], True
            raise BriefingUnavailable(None) from exc
        self._cache[key] = (now, value)
        return value, False

    # ---------- 원자료 ----------
    def weather_data(self, lat, lon):
        lat, lon = round(lat, 2), round(lon, 2)
        query = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code",
            "hourly": "precipitation_probability,weather_code", "daily": "temperature_2m_max,temperature_2m_min",
            "timezone": "Asia/Seoul", "forecast_days": 1})

        def load():
            raw = self.fetch(f"{OPEN_METEO}?{query}")
            current = raw["current"]
            now_hour = current["time"][:13]
            rain = None
            hourly = raw.get("hourly", {})
            for stamp, prob, code in zip(hourly.get("time", []), hourly.get("precipitation_probability", []),
                                         hourly.get("weather_code", [])):
                wet = weather_words(code)[2]
                if stamp[:13] > now_hour and prob is not None and prob >= 50 and wet:
                    rain = {"hour": int(stamp[11:13]), "prob": prob, "kind": wet}
                    break
            daily = raw.get("daily", {})
            return {"temp": float(current["temperature_2m"]), "code": int(current["weather_code"]),
                    "max": float(daily["temperature_2m_max"][0]), "min": float(daily["temperature_2m_min"][0]),
                    "rain": rain, "observed": current["time"]}
        return self._cached(("weather", lat, lon), WEATHER_TTL, load)

    def fx_data(self, today):
        start = (today - timedelta(days=10)).isoformat()

        def load():
            raw = self.fetch(f"{FRANKFURTER}/{start}..?from=KRW&to=USD,JPY,EUR")
            days = sorted(raw["rates"])
            if len(days) < 2:
                raise ValueError("not enough days")
            def won(day):
                r = raw["rates"][day]
                return {"USD": 1 / r["USD"], "JPY100": 100 / r["JPY"], "EUR": 1 / r["EUR"]}
            return {"date": days[-1], "latest": won(days[-1]), "previous": won(days[-2])}
        return self._cached(("fx", start), FX_TTL, load)

    # ---------- 낭독 문장 ----------
    def build(self, kind, *, lat=None, lon=None, remain_sec=None, remain_m=None, now=None):
        if kind not in KINDS:
            raise KeyError(kind)
        now = now or datetime.fromtimestamp(self.clock(), timezone.utc)
        try:
            lines, subtitle, stale = getattr(self, f"_{kind}")(lat=lat, lon=lon, remain_sec=remain_sec,
                                                               remain_m=remain_m, now=now)
            available = True
        except BriefingUnavailable as exc:
            message = exc.args[0] if exc.args and exc.args[0] else f"지금은 {TITLES[kind]} 정보를 불러오지 못했어요."
            lines, subtitle, stale, available = [message], "정보 없음", False, False
        text = " ".join(lines)
        return {"id": kind, "kind": "briefing", "title": TITLES[kind], "subtitle": subtitle, "lines": lines,
                "text": text, "duration_sec": round(max(3.0, len(text.replace(" ", "")) / CHARS_PER_SEC), 1),
                "available": available, "stale": stale, "tts": "device",
                "generated_at": now.astimezone(KST).isoformat(timespec="seconds"), "sources": [SOURCES[kind]]}

    def _weather(self, *, lat, lon, now, **_):
        if lat is None or lon is None:
            raise BriefingUnavailable("위치 정보를 받지 못해서 날씨를 알려 드릴 수 없어요.")
        data, stale = self.weather_data(lat, lon)
        label, spoken, _ = weather_words(data["code"])
        lines = ["현재 위치 기준 날씨를 알려 드릴게요.",
                 f"지금은 {spoken} 기온은 {read_temperature(data['temp'])}예요.",
                 f"오늘 최고 기온은 {read_temperature(data['max'])}, 최저 기온은 {read_temperature(data['min'])}예요."]
        rain = data["rain"]
        if rain:
            lines.append(f"{read_clock(rain['hour'], 0)}쯤 {rain['kind']} 소식이 있어요.")
            lines.append("우산을 챙기세요." if rain["kind"] == "비" else "길이 미끄러울 수 있어요.")
        else:
            lines.append("오늘은 비나 눈 소식이 없어요.")
        subtitle = f"현재 위치 · {label} {round(data['temp'])}°"
        if rain:
            subtitle += f" · {'오전' if rain['hour'] < 12 else '오후'} {rain['kind']} 소식"
        return lines, subtitle, stale

    def _economy(self, *, now, **_):
        data, stale = self.fx_data(now.astimezone(KST).date())
        names = [("USD", "미국 달러는", "달러"), ("JPY100", "일본 엔화는 백 엔에", "엔(100)"), ("EUR", "유로는", "유로")]
        lines, parts = ["주요 환율을 알려 드릴게요."], []
        for code, spoken, short in names:
            value, before = data["latest"][code], data["previous"][code]
            diff = round(value) - round(before)
            lines.append(f"{spoken} {read_int(round(value))} 원이에요.")
            if code == "USD":
                lines.append("전날과 거의 같아요." if diff == 0 else
                             f"전날보다 {read_int(abs(diff))} 원 {'올랐어요' if diff > 0 else '내렸어요'}.")
            arrow = "" if diff == 0 else (f" ▲{diff}" if diff > 0 else f" ▼{-diff}")
            parts.append(f"{short} {round(value):,}원{arrow if code == 'USD' else ''}")
        day = date.fromisoformat(data["date"])
        lines.append(f"{read_month_day(day.month, day.day)} 유럽중앙은행 기준이에요.")
        return lines, " · ".join(parts), stale

    def _traffic(self, *, remain_sec, remain_m, now, **_):
        if remain_sec is None:
            raise BriefingUnavailable("경로 안내 중이 아니라서 교통 정보를 알 수 없어요.")
        local = now.astimezone(KST)
        eta = local + timedelta(seconds=remain_sec)
        minutes = remain_sec / 60
        lines = ["목적지까지 가는 길을 알려 드릴게요."]
        if remain_m is not None:
            km = remain_m / 1000
            lines.append(f"남은 거리는 약 {read_number(km, 0 if km >= 10 else 1)} 킬로미터예요.")
        lines += [f"예상 소요 시간은 {read_minutes(minutes)}이에요.",
                  f"{read_clock(eta.hour, eta.minute)}쯤 도착할 예정이에요."]
        subtitle = "내 경로 기준 · " + (f"{remain_m / 1000:.0f}km · " if remain_m is not None else "") + \
                   f"{max(1, round(minutes))}분 · {eta:%H:%M} 도착"
        return lines, subtitle, False
