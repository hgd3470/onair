from copy import deepcopy

from teum.playlist.queue_candidates import next_candidates
from test_recommendation import story, ids


def test_refill_excludes_reserved_and_consumed_without_mutating_inputs():
    contents = [story(str(i), no=i) for i in range(10)]
    original = deepcopy(contents)
    queued = ["1", "2", "3"]
    result = next_candidates(contents, {}, current_id="0", queued_ids=queued,
                             consumed_ids=["4"])
    assert ids(result) == ["5", "6"]
    assert queued == ["1", "2", "3"] and contents == original
    assert next_candidates(contents, {}, queued_ids=list("01234")) == []


def test_budget_topic_and_supply_shortage():
    contents = [story("a"), story("b", "C01"), story("e", "C21")]
    assert ids(next_candidates(contents, {}, mode="pick", interests=["C06"],
                               remaining_sec=30, speed=2)) == ["a"]
    assert next_candidates(contents, {}, remaining_sec=59) == []
    assert ids(next_candidates(contents, {}, consumed_ids=["a"])) == ["b"]
