import pytest

from teum.playlist.ranker import rank_candidates
from teum.playlist.recommendation import recommend


def story(cid, cat="C06", no=1, **extra):
    return dict(content_id=cid, kind="story", title=cid, category=cat,
                duration_sec=60, audio_uri=f"/audio/{cid}.mp3", tags=[], play_no=no, **extra)


def ids(result):
    return [c["content_id"] for c in result]


def test_reactions_transfer_and_cancel():
    contents = [story("old", no=0), story("new", no=3), story("other", "C01", 1)]
    history = {"old": {"liked": True, "completed": True}}
    ranked, _ = rank_candidates(contents, history)
    assert ids(ranked) == ["new", "other"]
    assert "positive_feedback" in ranked[0]["reason_codes"]
    history["old"] = {"liked": False, "saved": False, "skipped": True}
    ranked, _ = rank_candidates(contents, history)
    assert ids(ranked) == ["old", "other", "new"]
    assert "not_yet_heard" in ranked[0]["reason_codes"]


def test_save_weaker_than_like_and_no_history_penalty():
    contents = [story("a"), story("b", "C01", 0)]
    liked, _ = rank_candidates(contents, {"a": {"liked": True}})
    saved, _ = rank_candidates(contents, {"a": {"saved": True}})
    assert ids(liked)[0] == ids(saved)[0] == "a"
    # Compare transferable category contributions, without direct saved boost.
    contents.append(story("new", no=3))
    liked, _ = rank_candidates(contents, {"a": {"liked": True, "completed": True}})
    saved, _ = rank_candidates(contents, {"a": {"saved": True, "completed": True}})
    assert liked[0]["recommendation_score"] > saved[0]["recommendation_score"]


def test_strict_pick_religion_and_published_p01():
    contents = [story("a", "C01"), story("b"), story("e", "C21"), story("w1-p01-01", "C04")]
    assert ids(rank_candidates(contents, {})[0]) == ["a", "b", "w1-p01-01"]
    assert ids(rank_candidates(contents, {}, interests={"C06"}, groups={"B"}, mode="pick")[0]) == ["b"]
    assert ids(rank_candidates(contents, {}, interests={"C21"}, groups={"E"}, mode="pick")[0]) == ["e"]


def test_diversity_baseline_and_determinism():
    contents = [story("b3", no=3), story("b2", no=2), story("b1", no=1), story("a", "C01", 4)]
    ranked, _ = rank_candidates(contents, {}, interests={"C06"})
    assert ids(ranked) == ["b1", "b2", "a", "b3"]
    assert ranked == rank_candidates(list(reversed(contents)), {}, interests={"C06"})[0]
    assert ids(rank_candidates(contents, {}, baseline=True)[0]) == ["b1", "b2", "b3", "a"]


@pytest.mark.parametrize("minutes,history,mode,groups,code", [
    (15, {}, "pick", {"C"}, "no_matching_topic"),
    (15, {"a": {"completed": True}}, "recommend", set(), "all_consumed"),
    (.5, {}, "recommend", set(), "no_item_fits_budget"),
    (15, {}, "recommend", set(), "partial_catalog"),
    (1, {}, "recommend", set(), None),
])
def test_shortages(minutes, history, mode, groups, code):
    result = recommend([story("a")], minutes, history, interests={"C13"} if mode == "pick" else (), mode=mode, groups=groups)
    assert result["shortage"]["code"] == code
    assert result["total_sec"] <= result["budget_sec"]
