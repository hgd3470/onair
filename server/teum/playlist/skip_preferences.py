"""Conservative skip features from stored, explicitly tagged user-next events."""
from collections import defaultdict
import math


PROFILE_VERSION = "skip-timing-v1"


def skip_penalties(contents, history, events, *, now):
    catalog = {c["content_id"]: c for c in contents if c.get("kind") == "story"}
    grouped = defaultdict(list)
    for e in events:
        if e.get("content_id") in catalog and e.get("playback_id") and e.get("type") in {
                "play", "position", "pause", "resume", "skip", "complete", "duck_start", "duck_end"}:
            grouped[e["content_id"]].append(e)
    penalties = {}
    for cid, rows in grouped.items():
        h = history.get(cid, {})
        if h.get("liked") or h.get("saved") or h.get("completed"):
            continue
        rows = sorted(rows, key=lambda e: (e["seq"], e["event_id"]))
        playback = [e for e in rows if e["playback_id"] == rows[-1]["playback_id"]]
        first, last = playback[0], playback[-1]
        if (first["type"] != "play" or first["position_sec"] != 0 or last["type"] != "skip"
                or last.get("skip_reason") != "user_next"
                or any(e["type"] in {"pause", "resume", "duck_start", "duck_end", "complete"}
                       for e in playback)):
            continue
        duration = catalog[cid]["duration_sec"]
        if not math.isfinite(duration) or duration <= 0:
            continue
        # Catalog lacks version metadata: changed-version evidence stays neutral.
        if any(e.get("content_version", 1) != 1 for e in playback):
            continue
        valid, active_wall = True, 0.0
        for a, b in zip(playback, playback[1:]):
            delta, elapsed = b["position_sec"] - a["position_sec"], b["client_ts"] - a["client_ts"]
            rate = a.get("playback_rate", 1.0)
            if (not all(math.isfinite(v) for v in (delta, elapsed, rate, b["position_sec"]))
                    or not 0 < rate <= 3 or elapsed < 0 or delta < 0
                    or not 0 <= b["position_sec"] <= duration or abs(delta - rate * elapsed) > 1):
                valid = False
                break
            if delta > 0:
                active_wall += delta / rate
        age = now.timestamp() - last["client_ts"]
        if not valid or active_wall <= 0 or not 0 <= age <= 30 * 86400:
            continue
        coverage = sum(b["position_sec"] - a["position_sec"]
                       for a, b in zip(playback, playback[1:])) / duration
        if coverage >= .8:
            continue
        early = active_wall <= 10 and coverage < .2
        penalties[cid] = {"penalty": (0.5 if early else 0.2) * 2 ** (-age / 86400 / 14),
                          "kind": "early_skip" if early else "mid_skip",
                          "coverage": coverage, "active_wall_sec": active_wall}
    return penalties
