"""추천 틀(recsys): 기준선이 기존 /playlist와 같은지, 전략·기록·차량 맥락·EASE가 도는지 본다."""
import random

import pytest

from conftest import API
from test_listening import event, register
from teum.recsys import assign, ease
from teum.recsys.strategies import rerank, reorder


def ids(response):
    assert response.status_code == 200, response.text
    return [c["content_id"] for c in response.json()["items"]]


def test_rule_matches_existing_playlist(client):
    user, auth = register(client)
    client.post(f"{API}/events", headers=auth, json=event(1, "save", 0, "story-a03"))
    params = dict(user_id=user, minutes=10)
    base = ids(client.get(f"{API}/playlist", params=params))
    body = client.get(f"{API}/recsys/playlist", params={**params, "algo": "rule"})
    assert ids(body) == base
    rs = body.json()["recsys"]
    assert rs["algorithm"] == "rule" and rs["assignment"] == "request" and rs["request_id"]


def test_default_is_rule_and_serve_log_is_written(client):
    user, _ = register(client)
    first = client.get(f"{API}/recsys/playlist", params=dict(user_id=user, minutes=5))
    assert first.json()["recsys"]["algorithm"] == "rule"
    client.get(f"{API}/recsys/playlist", params=dict(user_id=user, minutes=5, log=False))
    status = client.get(f"{API}/recsys/status").json()
    assert status["served_requests"] == 1
    assert status["strategies"] == ["rule", "bandit", "ease"]


def test_remain_sec_sets_budget_and_context_is_logged(client):
    user, _ = register(client)
    body = client.get(f"{API}/recsys/playlist",
                      params=dict(user_id=user, remain_sec=120, passengers=2, hour=8)).json()
    assert body["budget_sec"] == pytest.approx(120, abs=1)
    assert body["recsys"]["context"] == {"remain_sec": 120.0, "passengers": 2, "hour": 8}
    assert body["signal_status"]["vehicle_context"] == "connected"  # 출근 시간대는 점수에 쓴다 (S2-11)
    night = client.get(f"{API}/recsys/playlist", params=dict(user_id=user, remain_sec=120, passengers=2, hour=23))
    assert night.json()["signal_status"]["vehicle_context"] == "logged_only"  # 동승자 수만으로는 바꾸지 않는다
    assert client.get(f"{API}/recsys/playlist", params=dict(user_id=user)).status_code == 422


def test_ease_without_model_falls_back_to_bandit(client):
    user, _ = register(client)
    rs = client.get(f"{API}/recsys/playlist", params=dict(user_id=user, minutes=5, algo="ease")).json()["recsys"]
    assert rs["algorithm"] == "bandit" and rs["requested"] == "ease"
    assert rs["fallback"] == "ease_model_not_ready"


def story(cid, cat, score):
    return {"content_id": cid, "kind": "story", "category": cat, "recommendation_score": score,
            "play_no": None, "duration_sec": 60, "audio_uri": f"/gen/{cid}.mp3"}


def test_bandit_promotes_item_with_strong_feedback():
    ranked = [story("a", "C01", 1.0), story("b", "C02", 0.9), story("c", "C03", 0.8)]
    stats = {"c": [40, 0], "a": [0, 40]}
    out, used, _ = rerank("bandit", ranked, set(), stats=stats, rng=random.Random(3))
    assert used == "bandit" and out[0]["content_id"] == "c" and out[-1]["content_id"] == "a"
    assert out[0]["rule_score"] == 0.8 and out[0]["recsys"]["evidence"] == 40


def test_bandit_is_reproducible_with_same_seed():
    ranked = [story(str(i), f"C0{i % 5 + 1}", 1.0) for i in range(8)]
    a = [c["content_id"] for c in rerank("bandit", ranked, set(), stats={}, rng=random.Random(9))[0]]
    b = [c["content_id"] for c in rerank("bandit", ranked, set(), stats={}, rng=random.Random(9))[0]]
    assert a == b


def test_reorder_keeps_no_same_category_twice_in_a_row():
    ranked = [story("a1", "C01", 3), story("a2", "C01", 2.9), story("a3", "C01", 2.8), story("b1", "C06", 1)]
    out = [c["category"] for c in reorder(ranked, set())]
    assert all(not (out[i] == out[i - 1] == out[i - 2]) for i in range(2, len(out)))


def test_ease_learns_co_listening():
    users = {f"u{i}": {"a", "b"} for i in range(30)} | {f"v{i}": {"c", "d"} for i in range(30)}
    model = ease.fit(users, lam=5.0)
    s = ease.scores(model, ["a"])
    assert s["b"] > s["c"] and s["b"] > s["d"]
    ranked = [story("c", "C01", 1.0), story("b", "C02", 1.0), story("d", "C03", 1.0)]
    out, used, _ = rerank("ease", ranked, set(), stats={}, model=model, liked=["a"])
    assert used == "ease" and out[0]["content_id"] == "b"


def test_ease_model_file_round_trip(tmp_path):
    model = ease.fit({f"u{i}": {"a", "b"} for i in range(3)})
    ease.save(model, tmp_path / "m.json")
    assert ease.load(tmp_path / "m.json")["items"] == ["a", "b"]
    assert ease.load(tmp_path / "missing.json") is None


def test_split_assignment_is_stable_and_validated():
    env = {"TEUM_RECSYS_SPLIT": "rule:50,bandit:50"}
    picks = {assign.choose(f"user-{i}", env=env)[0] for i in range(200)}
    assert picks == {"rule", "bandit"}
    assert assign.choose("same", env=env) == assign.choose("same", env=env)
    assert assign.choose("x", "ease", env=env) == ("ease", "request")
    with pytest.raises(ValueError):
        assign.parse_split("nope:1")


@pytest.mark.parametrize("extra", [dict(interests="science,funny"), dict(interests="history", mode="pick"),
                                   dict(interests="funny", speed=1.5)])
def test_rule_matches_existing_playlist_with_interests_and_pick(client, extra):
    user, auth = register(client)
    client.post(f"{API}/events", headers=auth, json=event(1, "like", 0, "story-a02"))
    params = dict(user_id=user, minutes=4, **extra)
    base = client.get(f"{API}/playlist", params=params).json()
    mine = client.get(f"{API}/recsys/playlist", params={**params, "algo": "rule"}).json()
    assert [c["content_id"] for c in mine["items"]] == [c["content_id"] for c in base["items"]]
    for key in ("total_sec", "budget_sec", "leftover_sec", "packing", "shortage", "taxonomy_version"):
        assert mine[key] == base[key]


def test_reorder_is_identity_on_ranker_output():
    from teum.playlist.ranker import rank_candidates
    contents = [story(f"s{i}", cat, 0) for i, cat in enumerate(["C01", "C01", "C02", "C06", "C06", "C06", "C13", "C01"])]
    for c in contents:
        c.update(tags=[], title=c["content_id"])
    ranked, _ = rank_candidates(contents, {}, ["C01", "C06"], {"A", "B"})
    assert [c["content_id"] for c in reorder(ranked, {"A", "B"})] == [c["content_id"] for c in ranked]


def test_signals_read_listen_state(tmp_path):
    import sqlite3
    from teum.recsys import signals
    db = sqlite3.connect(tmp_path / "e.sqlite")
    db.execute("CREATE TABLE listen_state (user_id, content_id, completed, skipped, liked, saved, played_sec)")
    db.executemany("INSERT INTO listen_state VALUES (?,?,?,?,?,?,?)", [
        ("u1", "a", 1, 0, 0, 0, 60), ("u2", "a", 0, 1, 0, 0, 5), ("u3", "a", 0, 0, 1, 0, 10),
        ("u1", "b", 0, 0, 0, 1, 3), ("u2", "b", 0, 0, 0, 0, 3)])
    db.commit()
    db.close()
    assert signals.content_stats(tmp_path / "e.sqlite") == {"a": [2, 1]}
    assert signals.positives(tmp_path / "e.sqlite") == {"u1": {"a", "b"}, "u3": {"a"}}
    assert signals.content_stats(tmp_path / "missing.sqlite") == {}
    (tmp_path / "bad.sqlite").write_bytes(b"not a database")
    assert signals.content_stats(tmp_path / "bad.sqlite") == {}


def test_ease_falls_back_when_liked_items_unknown_to_model():
    model = ease.fit({f"u{i}": {"a", "b"} for i in range(30)} | {f"v{i}": {"c", "d"} for i in range(80)})
    ranked = [story("c", "C01", 1.0), story("b", "C02", 1.0)]
    _, used, reason = rerank("ease", ranked, set(), stats={}, model=model, liked=["zzz"])
    assert (used, reason) == ("bandit", "no_known_positive_in_model")
    _, used, reason = rerank("ease", ranked, set(), stats={}, model=model, liked=[])
    assert (used, reason) == ("bandit", "no_positive_history")


def test_bandit_prior_does_not_double_count_and_stays_bounded():
    from teum.recsys.strategies import bandit_bonuses
    ranked = [story("a", "C01", 1), story("b", "C01", 1), story("new", "C01", 1)]
    _, d = bandit_bonuses(ranked, {"a": [500, 0], "b": [0, 500]}, random.Random(1))
    assert d["new"]["posterior_mean"] == pytest.approx(0.5)            # 카테고리 50% → 출발값 Beta(3,3)
    assert d["a"]["posterior_mean"] > 0.99 and d["b"]["posterior_mean"] < 0.01


def test_serve_log_failure_does_not_break_playlist(client, tmp_path):
    user, _ = register(client)
    client.get(f"{API}/recsys/status")  # 추천 서비스 객체를 만든다
    service = client.app.state.recsys
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    service.serve_log.db_path = blocker / "serve.sqlite"  # 파일 아래 경로라 만들 수 없다
    service.serve_log._ready = False
    body = client.get(f"{API}/recsys/playlist", params=dict(user_id=user, minutes=5)).json()
    assert body["items"] and body["recsys"]["request_id"] is None and body["recsys"]["log_error"]


def test_split_rejects_negative_and_nan():
    for bad in ("rule:-10,bandit:60", "rule:nan", "rule:inf"):
        with pytest.raises(ValueError):
            assign.parse_split(bad)
