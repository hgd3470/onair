"""Business invariants, boundary cases and real HTTP policy replacement."""
from copy import deepcopy
import json
import random

import pytest

from conftest import API
from teum.playlist.policy import PlaylistPolicy
from teum.playlist.policy_provider import get_policy_provider, load_policy
from teum.playlist.recommendation import recommend


def content(cid, seconds, cat="C01", kind="story"):
    return dict(content_id=cid, title=cid, kind=kind, category=cat,
                duration_sec=seconds, audio_uri=f"/audio/{cid}.mp3", tags=[], play_no=1)


def ad(seconds=30, cid="ad"):
    return content(cid, seconds, "ad", "ad")


def assert_invariants(result, policy=PlaylistPolicy()):
    rows = result["items"]
    if rows:
        assert rows[0]["kind"] == rows[-1]["kind"] == "story"
    stories = [r for r in rows if r["kind"] == "story"]
    assert len(stories) == len({r["content_id"] for r in stories})
    for a, b in zip(rows, rows[1:]):
        assert not a["kind"] == b["kind"] == "ad"
        assert b["start_at_sec"] == pytest.approx(a["start_at_sec"] + a["duration_sec"])
    total = sum(r["duration_sec"] for r in rows)
    assert total == pytest.approx(result["total_sec"])
    assert total <= result["budget_sec"] + 1e-8
    assert result["advertising"]["actual_ratio"] <= policy.ad_max_ratio + 1e-8
    assert result["leftover_sec"] == pytest.approx(result["budget_sec"] - total)
    if result["advertising"]["minimum_met"]:
        assert result["advertising"]["actual_ratio"] + 1e-8 >= policy.ad_min_ratio


def test_floor_ceiling_and_ads_inside_final_three_minutes():
    # No seven/three-minute exclusion: 90s story, 20s ad, 90s story.
    result = recommend([content("a", 90), content("b", 90), ad(20)], 200 / 60, {})
    assert [r["kind"] for r in result["items"]] == ["story", "ad", "story"]
    assert result["advertising"]["actual_ratio"] == .10
    assert result["advertising"]["minimum_met"]
    assert_invariants(result)


def test_whole_ads_never_round_above_ceiling():
    result = recommend([content("a", 40), content("b", 40), ad(30)], 2, {})
    assert all(r["kind"] == "story" for r in result["items"])
    assert result["advertising"]["reason"] == "no_compliant_ad_plan_found"
    assert_invariants(result)


def test_no_story_no_ads_and_one_story_no_ads():
    for contents in ([ad()], [content("a", 120), ad()]):
        result = recommend(contents, 10, {})
        assert all(r["kind"] == "story" for r in result["items"])
        assert not result["advertising"]["minimum_met"]
        assert_invariants(result)


def test_speed_only_changes_stories_and_no_cut_or_duplicate():
    result = recommend([content("a", 180), content("b", 180), ad(20)], 200 / 60, {}, speed=2)
    assert [r["duration_sec"] for r in result["items"]] == [90, 20, 90]
    assert [r["playback_rate"] for r in result["items"]] == [2, 1, 2]
    assert_invariants(result)


def test_ad_mixture_finds_feasible_duration_and_input_is_unchanged():
    contents = [content("a", 100), content("b", 100), content("c", 100), ad(20), ad(15, "ad2")]
    original = deepcopy(contents)
    result = recommend(contents, 335 / 60, {})
    assert result["advertising"]["planned_sec"] == 35
    assert result["total_sec"] == 335
    assert contents == original
    assert_invariants(result)


def test_unique_ads_before_repeats_even_with_different_durations():
    contents = [content(str(i), 100) for i in range(6)] + [ad(20, "a20"), ad(30, "a30"), ad(40, "a40")]
    result = recommend(contents, 12, {})
    assert result["advertising"]["minimum_met"]
    assert result["advertising"]["repeat_count"] == 0
    assert result["advertising"]["unique_creatives"] >= 2
    assert_invariants(result)


def test_ad_repeat_only_when_unique_inventory_cannot_meet_floor():
    contents = [content(str(i), 100) for i in range(6)] + [ad(30)]
    result = recommend(contents, 12, {})
    assert result["advertising"]["minimum_met"]
    assert result["advertising"]["unique_creatives"] == 1
    assert result["advertising"]["repeat_count"] == 2
    assert_invariants(result)


def test_repeat_fallback_uses_different_length_creatives_before_second_round():
    contents = [content(str(i), 100) for i in range(10)] + [ad(20, "a"), ad(30, "b")]
    result = recommend(contents, 20, {})
    ads = [c["content_id"] for c in result["items"] if c["kind"] == "ad"]
    assert result["advertising"]["minimum_met"]
    assert result["advertising"]["repeat_count"] > 0
    assert set(ads[:2]) == {"a", "b"}
    assert_invariants(result)


def test_five_selected_groups_each_get_ten_percent_excluding_ads():
    contents = [content(f"a{i}", 60) for i in range(15)]
    contents += [content(g, 60, cat) for g, cat in [("b", "C06"), ("c", "C13"), ("d", "C18"), ("e", "C21")]]
    contents.append(ad())
    result = recommend(contents, 10, {"a0": {"liked": True}}, mode="pick",
                       interests=["C01", "C06", "C13", "C18", "C21"])
    assert len(result["topic_quotas"]) == 5
    assert all(q["minimum_met"] and q["actual_ratio"] >= .10 for q in result["topic_quotas"])
    assert sum(q["planned_sec"] for q in result["topic_quotas"]) == result["advertising"]["story_sec"]
    assert_invariants(result)


def test_pick_uses_preference_after_reserving_minimum_not_rolling_cap():
    contents = [content(f"a{i}", 60) for i in range(12)] + [content("b", 60, "C06")]
    result = recommend(contents, 10, {"a0": {"liked": True}}, mode="pick", interests=["C01", "C06"])
    categories = [r["category"] for r in result["items"]]
    assert categories.count("C01") == 9 and categories.count("C06") == 1
    assert all(q["minimum_met"] for q in result["topic_quotas"])
    assert result["advertising"]["reason"] == "no_eligible_ads"


def test_unavailable_topic_and_oversized_topic_are_reported_without_outside_fallback():
    for contents in ([content("a", 60), content("outside", 60, "C13")],
                     [content("a", 60), content("b", 600, "C06")]):
        result = recommend(contents, 2, {}, mode="pick", interests=["C01", "C06"])
        assert [r["content_id"] for r in result["items"]] == ["a"]
        missing = next(q for q in result["topic_quotas"] if q["group_id"] == "B")
        assert not missing["minimum_met"]
        assert missing["reason"] == "no_fitting_group_content"


def test_insufficient_topic_share_is_explicit_and_not_rounded_to_one_item():
    contents = [content("a", 500), content("b", 20, "C06")]
    result = recommend(contents, 10, {}, mode="pick", interests=["C01", "C06"])
    q = next(q for q in result["topic_quotas"] if q["group_id"] == "B")
    assert q["planned_sec"] == 20 and q["actual_ratio"] < .10
    assert not q["minimum_met"]


@pytest.mark.parametrize("changes", [
    {"ad_min_ratio": -.1}, {"ad_min_ratio": .2}, {"ad_max_ratio": 1},
    {"ad_min_ratio": float("nan")}, {"ad_max_ratio": float("inf")},
    {"ad_min_ratio": True}, {"selected_group_min_ratio": .21},
    {"policy_id": ""}, {"unexpected": 1}, {"ad_min_ratio": "0.1"},
])
def test_invalid_policy_rejected(changes):
    with pytest.raises(ValueError):
        PlaylistPolicy.from_dict(changes)


def test_policy_json_reload_default_and_validation(tmp_path, monkeypatch):
    monkeypatch.delenv("TEUM_PLAYLIST_POLICY_FILE", raising=False)
    assert load_policy() == PlaylistPolicy()
    path = tmp_path / "policy.json"
    monkeypatch.setenv("TEUM_PLAYLIST_POLICY_FILE", str(path))
    path.write_text(json.dumps({"policy_id": "ads-off", "ad_min_ratio": 0, "ad_max_ratio": 0}), encoding="utf-8")
    assert load_policy().ad_max_ratio == 0
    path.write_text('{"ad_min_ratio":0.10,"ad_min_ratio":0.12}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy()


def test_http_policy_provider_can_be_replaced_without_client_parameter(client):
    policy = PlaylistPolicy(policy_id="admin-revision-2", ad_min_ratio=0, ad_max_ratio=0)
    client.app.dependency_overrides[get_policy_provider] = lambda: lambda: policy
    r = client.get(f"{API}/playlist", params={"user_id": "u1", "minutes": 10}).json()
    assert r["policy"] == policy.to_dict()
    assert all(c["kind"] == "story" for c in r["items"])
    assert_invariants(r, policy)


def test_invalid_policy_fails_closed_but_legacy_is_unchanged(client, monkeypatch, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"ad_min_ratio":0.9}', encoding="utf-8")
    monkeypatch.setenv("TEUM_PLAYLIST_POLICY_FILE", str(path))
    params = {"user_id": "u1", "minutes": 10}
    assert client.get(f"{API}/playlist", params=params).status_code == 503
    assert client.get(f"{API}/playlist", params={**params, "mode": "legacy"}).status_code == 200


def test_generated_catalogs_preserve_hard_constraints():
    rng = random.Random(1112)
    for _ in range(50):
        contents = [content(str(i), rng.randint(20, 240) + .03, rng.choice(["C01", "C06", "C13"])) for i in range(12)]
        contents += [ad(rng.choice([15, 20, 30]), "ad1"), ad(rng.choice([15, 20, 30]), "ad2")]
        mode = rng.choice(["pick", "recommend"])
        result = recommend(contents, rng.randint(2, 20), {}, speed=rng.choice([1, 1.5, 2]),
                           mode=mode, interests=["C01", "C06", "C13"])
        assert_invariants(result)
        assert result == recommend(list(reversed(contents)), result["budget_sec"] / 60, {},
                                   speed=next(c["playback_rate"] for c in result["items"] if c["kind"] == "story"),
                                   mode=mode, interests=["C01", "C06", "C13"])
