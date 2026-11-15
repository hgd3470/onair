from collections import Counter
from copy import deepcopy

from teum.playlist.diversity import group_of
from teum.playlist.ranker import rank_candidates
from teum.playlist.queue_candidates import next_candidates
from teum.playlist.recommendation import recommend
from teum.playlist.topics import GROUPS
from test_recommendation import story, ids


def catalog():
    return ([story(f"b{i}", f"C{6+i%7:02}", i) for i in range(10)]
            + [story(f"a{i}", f"C{1+i%5:02}", 20+i) for i in range(10)])


def test_rank_rolling_five_cap_and_single_group_relaxation():
    contents = catalog()
    original = deepcopy(contents)
    ranked, _ = rank_candidates(contents, {}, interests=GROUPS["B"])
    assert max(Counter(group_of(c) for c in ranked[:5]).values()) <= 3
    for offset in range(len(ranked)-4):
        counts = Counter(group_of(c) for c in ranked[offset:offset+5])
        if max(counts.values()) > 3:
            # At the exhausted tail the only remaining group may exceed the cap.
            assert len({group_of(c) for c in ranked[offset+4:]}) == 1
    picked, _ = rank_candidates(contents, {}, interests=GROUPS["B"], mode="pick")
    assert len(picked) == 10 and all(group_of(c) == "B" for c in picked)
    fixed, _ = rank_candidates(contents, {}, interests=GROUPS["B"], baseline=True)
    assert ids(fixed)[:5] == [f"b{i}" for i in range(5)]
    assert contents == original


def test_refill_counts_existing_queue_and_leaves_it_unchanged():
    contents = catalog()
    queued = ["b0", "b1", "b2"]
    result = next_candidates(contents, {}, current_id="a0", queued_ids=queued,
                             consumed_ids=["a1"], interests=GROUPS["B"])
    assert len(result) == 2 and all(group_of(c) == "A" for c in result)
    assert not set(ids(result)) & {"a0", "a1", *queued}
    assert queued == ["b0", "b1", "b2"]


def test_shortage_and_time_filter_relax_without_outside_topics():
    contents = catalog()
    for c in contents:
        if group_of(c) == "A": c["duration_sec"] = 1000
    result = next_candidates(contents, {}, queued_ids=["b0", "b1", "b2"], remaining_sec=120,
                             interests=GROUPS["B"])
    assert len(result) == 2 and all(group_of(c) == "B" for c in result)
    empty = next_candidates(contents, {}, queued_ids=["b0", "b1", "b2"], remaining_sec=59)
    assert empty == []


def test_ads_count_as_slots_and_current_is_not_pending():
    contents = catalog() + [dict(content_id="ad", kind="ad", category="ad")]
    result = next_candidates(contents, {}, current_id="b3", queued_ids=["b0", "b1", "ad"],
                             interests=GROUPS["B"])
    assert Counter(group_of(c) for c in result) == {"A": 1, "B": 1}


def test_multi_pick_coverage_and_finite_packing():
    contents = catalog() + [story("faith", "C21", 0)]
    response = recommend(contents, 5, {}, interests=GROUPS["A"] | GROUPS["B"],
                         groups=["A", "B"], mode="pick")
    rows = response["items"]
    assert len(rows) == 5 and response["total_sec"] == 300
    # Finite pick guarantees airtime floors; it does not force a 3/5 split.
    assert {group_of(c) for c in rows} == {"A", "B"}
    assert all(q["minimum_met"] and q["actual_ratio"] >= .1 for q in response["topic_quotas"])
    assert "faith" not in ids(rows)
