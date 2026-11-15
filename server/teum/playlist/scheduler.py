"""이동시간 편성 (S1-11·S1-12, 추천 규칙 자리). 지금은 임시 규칙이다.

입력 = 남은 시간(분), 관심 분류, 청취 이력, 배속
출력 = 남은 시간 안에 들어가는 재생 목록(+광고 삽입)

규칙: 관심 분류 우선 → 안 들은 것 우선 → 관리 순서. 이야기 2편마다 광고 1편(데모 config 값).
"""


def build_playlist(contents, minutes, interests=None, history=None, speed=1.0, ad_every=2):
    budget = minutes * 60.0
    history = history or {}
    stories = [c for c in contents if c["kind"] == "story" and not history.get(c["content_id"], {}).get("completed")]
    ads = sorted((c for c in contents if c["kind"] == "ad"), key=lambda c: c["content_id"])
    interests = set(interests or [])

    def key(c):
        return (0 if c["category"] in interests else 1, 1 if c["content_id"] in history else 0, c.get("play_no") or 99)

    items, total, since_ad, ad_i = [], 0.0, 0, 0
    for c in sorted(stories, key=key):
        d = c["duration_sec"] / speed
        if total + d > budget:
            continue
        items.append({"content_id": c["content_id"], "kind": "story", "title": c["title"], "category": c["category"],
                      "duration_sec": round(d, 1), "start_at_sec": round(total, 1), "audio_uri": c["audio_uri"],
                      "attribution": c.get("attribution"), "license": c.get("license")})
        total += d
        since_ad += 1
        if since_ad == ad_every and ads:
            ad = ads[ad_i % len(ads)]
            ad_d = ad["duration_sec"] / speed
            if total + ad_d <= budget:
                items.append({"content_id": ad["content_id"], "kind": "ad", "title": ad["title"], "category": "ad",
                              "duration_sec": round(ad_d, 1), "start_at_sec": round(total, 1), "audio_uri": ad["audio_uri"]})
                total += ad_d
                ad_i += 1
            since_ad = 0
    return {"items": items, "total_sec": round(total, 1), "budget_sec": budget, "leftover_sec": round(budget - total, 1),
            "rule": "관심 분류 우선 → 안 들은 것 우선 → 관리 순서, 이야기 2편마다 광고"}
