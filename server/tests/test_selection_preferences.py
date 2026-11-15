from datetime import datetime, timedelta, timezone

import pytest

from test_recommendation import story, ids
from teum.playlist.ranker import rank_candidates
from teum.playlist.selection_preferences import TopicSelection, group_preferences

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


def selection(sid="one", groups=("B",), age=0, started=True):
    return TopicSelection(sid, NOW - timedelta(days=age), groups, started)


def test_empty_and_split_vote():
    assert group_preferences([], now=NOW) == {}
    assert group_preferences([selection(groups=("A", "B", "B"))], now=NOW) == {"A": .125, "B": .125}


def test_retries_order_and_daily_cap():
    single = group_preferences([selection()], now=NOW)
    assert group_preferences([selection()] * 20, now=NOW) == single
    repeated = [selection(str(i)) for i in range(10)]
    assert group_preferences(repeated, now=NOW)["B"] == pytest.approx(single["B"])
    assert group_preferences(list(reversed(repeated)), now=NOW) == group_preferences(repeated, now=NOW)


def test_recent_and_repeat_on_different_days():
    assert group_preferences([selection(age=1)], now=NOW)["B"] > group_preferences([selection(age=14)], now=NOW)["B"]
    assert group_preferences([selection(), selection("two", age=1)], now=NOW)["B"] > .25


def test_unstarted_expired_future_and_invalid_are_ignored():
    rows = [selection(started=False), selection(age=31), selection(age=-1), selection(groups=("X",)),
            selection(groups=()), TopicSelection("naive", NOW.replace(tzinfo=None), ("A",), True)]
    assert group_preferences(rows, now=NOW) == {}
    with pytest.raises(ValueError):
        group_preferences([], now=NOW.replace(tzinfo=None))


def test_conflicting_duplicate_is_order_independent():
    rows = [selection(), selection(groups=("A",))]
    assert group_preferences(rows, now=NOW) == group_preferences(rows[::-1], now=NOW) == {}


def test_group_preference_transfers_without_narrow_category_inference():
    contents = [story("a", "C01", 1), story("b1", "C06", 2), story("b2", "C07", 3)]
    ranked, _ = rank_candidates(contents, {}, selections=[selection()], now=NOW)
    assert ids(ranked)[0] == "b1"
    by_id = {c["content_id"]: c for c in ranked}
    assert by_id["b1"]["recommendation_score"] == by_id["b2"]["recommendation_score"]
    assert "past_topic_selection" in by_id["b2"]["reason_codes"]
    assert "past_topic_selection" not in by_id["a"]["reason_codes"]


def test_constraints_explicit_interest_and_baseline_still_win():
    contents = [story("a", "C01", 1), story("b", "C06", 2), story("e", "C21", 3)]
    rows = [selection(), selection("religion", ("E",), age=1)]
    ranked, _ = rank_candidates(contents, {}, interests=["C01"], selections=rows, now=NOW)
    assert ids(ranked)[0] == "a" and "e" not in ids(ranked)
    picked, _ = rank_candidates(contents, {}, interests=["C01"], mode="pick", selections=rows, now=NOW)
    assert ids(picked) == ["a"]
    baseline, _ = rank_candidates(contents, {}, baseline=True, selections=rows, now=NOW)
    assert ids(baseline) == ["a", "b"]
    assert all(c["reason_codes"] == ["catalog_order"] for c in baseline)




def test_multiple_selected_groups_preserve_learned_order():
    contents = [story("a", "C01", 1), story("b1", "C06", 2), story("b2", "C07", 3)]
    ranked, _ = rank_candidates(contents, {}, interests=["C01", "C06", "C07"], groups=["A", "B"],
                                mode="pick", selections=[selection()], now=NOW)
    assert ids(ranked) == ["b1", "a", "b2"]
    assert "past_topic_selection" in ranked[0]["reason_codes"]
    baseline, _ = rank_candidates(contents, {}, interests=["C01", "C06", "C07"], groups=["A", "B"],
                                  baseline=True, selections=[selection()], now=NOW)
    assert ids(baseline) == ["a", "b1", "b2"]




def test_window_edges_and_utc_day_boundaries():
    at_start = selection(age=30)
    assert group_preferences([at_start], now=NOW)["B"] > 0
    too_old = TopicSelection("old", at_start.selected_at - timedelta(microseconds=1), ("B",), True)
    assert group_preferences([too_old], now=NOW) == {}
    same_instant = TopicSelection("one", NOW.astimezone(timezone(timedelta(hours=9))), ("B",), True)
    assert group_preferences([selection(), same_instant], now=NOW) == group_preferences([selection()], now=NOW)
