from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest

from teum.playlist.skip_preferences import skip_penalties
from teum.playlist.ranker import rank_candidates
from test_recommendation import story, ids

NOW = datetime.now(timezone.utc)


def playback(seconds=8, rate=1, reason="user_next", start=0, age=0):
    end = NOW.timestamp() - age * 86400
    return [dict(event_id=str(uuid4()), content_id="a", playback_id="p", content_version=1,
                 seq=i, type=typ, position_sec=pos, playback_rate=rate, client_ts=ts,
                 skip_reason=reason if typ == "skip" else None)
            for i, typ, pos, ts in [(1, "play", start, end - seconds),
                                    (2, "skip", start + seconds * rate, end)]]


@pytest.mark.parametrize("seconds,rate,kind", [(8,1,"early_skip"),(10,1.5,"mid_skip"),
                                               (30,1,"mid_skip"),(50,1,None)])
def test_timing_and_rate(seconds, rate, kind):
    result = skip_penalties([story("a")], {}, playback(seconds, rate), now=NOW)
    if kind:
        assert result["a"]["kind"] == kind
        assert result["a"]["active_wall_sec"] == seconds
    else:
        assert result == {}


def test_ambiguous_telemetry_is_neutral():
    for rows in [playback(reason=None), playback(start=20), playback(age=31), playback(age=-1)]:
        assert skip_penalties([story("a")], {}, rows, now=NOW) == {}
    for typ in ["pause", "duck_start"]:
        rows = playback()
        rows.insert(1, {**rows[0], "event_id": str(uuid4()), "type":typ, "seq":1.5})
        assert skip_penalties([story("a")], {}, rows, now=NOW) == {}
    rows = playback(); rows[-1]["position_sec"] = 55  # seek, not real listening
    assert skip_penalties([story("a")], {}, rows, now=NOW) == {}


def test_latest_playback_decay_and_reactions():
    fresh = skip_penalties([story("a")], {}, playback(), now=NOW)["a"]["penalty"]
    old = skip_penalties([story("a")], {}, playback(age=14), now=NOW)["a"]["penalty"]
    assert old == pytest.approx(fresh / 2)
    for key in ["liked", "saved", "completed"]:
        assert skip_penalties([story("a")], {"a": {key: True}}, playback(), now=NOW) == {}
    rows = playback()
    rows.append({**rows[0], "seq":3, "playback_id":"new", "client_ts":NOW.timestamp()})
    assert skip_penalties([story("a")], {}, rows, now=NOW) == {}


def test_rank_changes_and_baseline_is_unchanged():
    contents = [story("a", "C06", 1), story("b", "C06", 2), story("c", "C01", 3)]
    ranked, _ = rank_candidates(contents, {}, events=playback(), now=NOW)
    assert ids(ranked) == ["b", "c", "a"]
    by_id = {c["content_id"]: c for c in ranked}
    assert by_id["b"]["recommendation_score"] == by_id["c"]["recommendation_score"] == .5
    assert by_id["a"]["recommendation_score"] == 0
    fixed, _ = rank_candidates(contents, {}, events=playback(), now=NOW, baseline=True)
    assert ids(fixed) == ["a", "b", "c"]
