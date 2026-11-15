"""S2-11: 차량 맥락 가산점이 관심사를 뒤집지 않고 순서만 바꾸는지 본다."""
from teum.recsys.context import context_bonuses, used_context
from teum.recsys.service import apply_context


def story(cid, cat, score):
    return {"content_id": cid, "kind": "story", "category": cat, "recommendation_score": score,
            "reason_codes": ["not_yet_heard"], "play_no": int(cid[1:])}


RANKED = [story("s1", "C17", 0.5), story("s2", "C02", 0.5), story("s3", "C03", 0.5),
          story("s4", "C16", 0.5), story("s5", "C01", 4.5)]


def test_family_raises_family_and_lowers_mystery():
    out = [c["content_id"] for c in apply_context(RANKED, set(), family=True)]
    assert out[0] == "s5"                       # 고른 관심사(+4)는 그대로 맨 앞
    assert out.index("s3") < out.index("s1") and out.index("s3") < out.index("s2")
    s3 = next(c for c in apply_context(RANKED, set(), family=True) if c["content_id"] == "s3")
    assert "family_context" in s3["reason_codes"] and s3["context_bonus"] == 1.0


def test_commute_hour_raises_news_only_in_morning():
    morning = [c["content_id"] for c in apply_context(RANKED, set(), hour=8)]
    assert morning.index("s4") < morning.index("s1")
    assert apply_context(RANKED, set(), hour=22) is RANKED   # 맥락이 없으면 그대로
    assert not used_context(hour=22) and used_context(hour=7) and used_context(family=True)


def test_bonus_is_small():
    bonuses = context_bonuses(RANKED, family=True, hour=8)
    assert all(abs(v) <= 1.5 for v, _ in bonuses.values())


def test_api_family_param(client):
    from conftest import API
    from test_listening import register
    user, _ = register(client)
    body = client.get(f"{API}/recsys/playlist", params=dict(user_id=user, minutes=5, family=True)).json()
    assert body["signal_status"]["vehicle_context"] == "connected"
    assert body["recsys"]["context"]["family"] is True
