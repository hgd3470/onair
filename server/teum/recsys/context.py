"""차량 맥락 가산점 (S2-11). 앱이 Pleos SDK에서 읽어 보낸 값으로 순서만 조금 바꾼다.

- family: 뒷좌석 탑승이 감지된 뒤 운전자가 "가족 콘텐츠로 바꿀까요?"에 동의했을 때만 true.
  아이 여부는 차량 신호로 알 수 없어서 좌석 탑승만으로는 바꾸지 않는다 (S2-11 조사 결과).
  → C03 가족·육아를 올리고, 아이와 듣기 어색한 C02 연애·결혼, C17 사건·미스터리는 내린다.
- hour: 앱 기준 현지 시각(0~23). 출근 시간대(6~9시)면 C16 뉴스·시사, C08 생활경제를 조금 올린다.

가산점은 ±1 이하라 고른 관심사 점수(+4)를 뒤집지 않는다. 고른 관심사 안에서, 또는 관심사 밖 후보끼리의
순서만 바뀐다.
"""
FAMILY_BOOST = {"C03": 1.0}
FAMILY_DEMOTE = {"C02": -1.0, "C17": -1.0}
COMMUTE_HOURS = range(6, 10)
COMMUTE_BOOST = {"C16": 0.5, "C08": 0.5}


def context_bonuses(ranked, *, family=False, hour=None):
    """content_id → (가산점, 이유 코드 목록). 쓸 맥락이 없으면 빈 dict."""
    rules = []
    if family:
        rules.append(({**FAMILY_BOOST, **FAMILY_DEMOTE}, "family_context"))
    if hour is not None and hour in COMMUTE_HOURS:
        rules.append((COMMUTE_BOOST, "commute_briefing"))
    out = {}
    for c in ranked:
        total, reasons = 0.0, []
        for table, reason in rules:
            value = table.get(c.get("category"), 0.0)
            if value:
                total += value
                if value > 0:
                    reasons.append(reason)
        if total:
            out[c["content_id"]] = (total, reasons)
    return out


def used_context(family=False, hour=None):
    return bool(family) or (hour is not None and hour in COMMUTE_HOURS)
