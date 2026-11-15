"""Exercise the real HTTP route and existing listening store, not mock rankers."""
import pytest

from conftest import API
from test_listening import event, register
from teum.catalog.categories import CODES, GROUPS as CANONICAL_GROUPS
from teum.playlist.ranker import ALGORITHM_VERSION
from teum.playlist.topics import CATEGORY_CODES, GROUPS


def items(response):
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_default_http_uses_reactions_and_preserves_response_fields(client):
    user, auth = register(client)
    params = dict(user_id=user, minutes=10)
    first = client.get(f"{API}/playlist", params=params)
    assert items(first)[0]["content_id"] == "story-a01"
    assert client.post(f"{API}/events", headers=auth,
                       json=event(1, "save", 0, "story-a03")).status_code == 200
    result = client.get(f"{API}/playlist", params=params)
    assert items(result)[0]["content_id"] == "story-a03"
    body = result.json()
    assert body["algorithm_version"] == ALGORITHM_VERSION
    assert {"items", "total_sec", "budget_sec", "leftover_sec", "rule"} <= body.keys()
    assert {"content_id", "duration_sec", "start_at_sec", "audio_uri"} <= body["items"][0].keys()
    assert body["signal_status"]["skip_timing"] == "connected"  # S2-10에서 연결
    assert body["signal_status"]["selection_history"] == "connected"
    assert client.post(f"{API}/events", headers=auth,
                       json=event(2, "unsave", 0, "story-a03")).status_code == 200
    assert items(client.get(f"{API}/playlist", params=params))[0]["content_id"] == "story-a01"


@pytest.mark.parametrize("query", [None, "", "  ,  "])
def test_saved_interest_fallback_explicit_priority_and_user_scope(client, query):
    user, auth = register(client)
    other, _ = register(client)
    assert client.put(f"{API}/me/interests", headers=auth,
                      json={"interests": ["funny"]}).status_code == 200
    params = dict(user_id=user, minutes=10)
    if query is not None:
        params["interests"] = query
    assert items(client.get(f"{API}/playlist", params=params))[0]["content_id"] == "story-a03"
    assert items(client.get(f"{API}/playlist", params={**params, "interests": " science,science "}))[0]["content_id"] == "story-a01"
    assert items(client.get(f"{API}/playlist", params={**params, "user_id": other}))[0]["content_id"] == "story-a01"
    assert client.get(f"{API}/me/interests", headers=auth).json()["interests"] == ["funny"]


def test_pick_canonical_codes_and_supply_independent_validation(client):
    for c, category in zip(client.app.state.catalog.stories, ["C06", "C13", "C21"]):
        c["category"] = category
    params = dict(user_id="u1", minutes=10, mode="pick", interests="C06,C07")
    result = items(client.get(f"{API}/playlist", params=params))
    assert [c["content_id"] for c in result] == ["story-a01"]
    empty = client.get(f"{API}/playlist", params={**params, "interests": "C20"})
    assert items(empty) == []
    assert empty.json()["shortage"]["code"] == "no_matching_topic"
    for invalid in ["A", "P01", "C99", ""]:
        assert client.get(f"{API}/playlist", params={**params, "interests": invalid}).status_code == 422
    recommended = items(client.get(f"{API}/playlist", params={"user_id": "u1", "minutes": 10}))
    assert all(c["category"] != "C21" for c in recommended)


def test_legacy_opt_in_keeps_old_order_and_reads_saved_interests(client):
    user, auth = register(client)
    client.post(f"{API}/events", headers=auth, json=event(1, "save", 0, "story-a03"))
    params = dict(user_id=user, minutes=10, mode="legacy")
    result = client.get(f"{API}/playlist", params=params)
    assert items(result)[0]["content_id"] == "story-a01"
    assert "algorithm_version" not in result.json()
    client.put(f"{API}/me/interests", headers=auth, json={"interests": ["funny"]})
    assert items(client.get(f"{API}/playlist", params=params))[0]["content_id"] == "story-a03"


def test_missing_audio_is_excluded_without_publishing_hidden_content(client):
    catalog = client.app.state.catalog
    catalog.audio_path(catalog.get("story-a01")["audio_uri"]).unlink()
    result = items(client.get(f"{API}/playlist", params={"user_id": "u1", "minutes": 10}))
    assert not {"story-a01", "story-x01"} & {c["content_id"] for c in result}


@pytest.mark.parametrize("overrides", [{"minutes": "nan"}, {"speed": "inf"}, {"user_id": " "}, {"mode": "wrong"}])
def test_invalid_input_is_rejected(client, overrides):
    assert client.get(f"{API}/playlist", params={"user_id": "u1", "minutes": 10, **overrides}).status_code == 422


def test_recommendation_topics_are_derived_from_pr10():
    assert CATEGORY_CODES == frozenset(CODES)
    assert GROUPS == {g: frozenset(codes) for g, codes in CANONICAL_GROUPS.items()}
