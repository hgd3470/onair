"""S1-11: deterministic content-based ranking, independent of time packing."""
import math
import unicodedata
from collections import defaultdict
from teum.playlist.topics import GROUPS, TAXONOMY_VERSION
from teum.playlist.selection_preferences import SELECTION_WEIGHT, group_preferences
from datetime import datetime, timezone
from teum.playlist.skip_preferences import skip_penalties
from teum.playlist.diversity import choose_index

ALGORITHM_VERSION = "r1-group-cap-direct-skip-v5"


def tags(content):
    return {unicodedata.normalize("NFKC", str(t)).strip().casefold()
            for t in content.get("tags", []) if str(t).strip()}


def management_key(content):
    number = content.get("play_no")
    return (number is None, number if number is not None else 0, content["content_id"])


def eligible(content):
    duration = content.get("duration_sec", 0)
    return (content.get("kind") == "story" and isinstance(duration, (int, float))
            and math.isfinite(duration) and duration > 0 and bool(content.get("audio_uri")))


def rank_candidates(contents, history, interests=(), groups=(), mode="recommend", baseline=False,
                    selections=(), now=None, events=()):
    """contents must already be channel/published-filtered by Catalog.

    Unlike/unsave remove positive evidence; skipped alone is never negative.
    Completed items remain profile evidence but cannot enter the automatic feed.
    """
    interests, groups = set(interests), set(groups)
    explicit = interests
    preferences = group_preferences(selections, now=now or datetime.now(timezone.utc))
    skips = {} if baseline else skip_penalties(contents, history, events, now=now or datetime.now(timezone.utc))
    category_sum, category_count = defaultdict(float), defaultdict(int)
    tag_sum, tag_count = defaultdict(float), defaultdict(int)
    for content in contents:
        if not eligible(content):
            continue
        h = history.get(content["content_id"], {})
        value = min(1, .7 * bool(h.get("liked")) + .3 * bool(h.get("saved"))
                    + .2 * bool(h.get("completed")))
        if not value:
            continue
        cat = content["category"]
        category_sum[cat] += value
        category_count[cat] += 1
        for tag in tags(content):
            tag_sum[tag] += value
            tag_count[tag] += 1

    available = [c for c in contents if eligible(c)
                 and (c["category"] != "C21" or "C21" in explicit)]
    if mode == "pick":
        available = [c for c in available if c["category"] in explicit]
    candidates = []
    for content in available:
        h = history.get(content["content_id"], {})
        if h.get("completed"):
            continue
        affinity = category_sum[content["category"]] / (3 + category_count[content["category"]])
        ts = tags(content)
        tag_affinity = sum(tag_sum[t] / (3 + tag_count[t]) for t in ts) / len(ts) if ts else 0
        unheard = h.get("played_sec", 0) <= 0
        selected_affinity = next((preferences.get(g, 0) for g, codes in GROUPS.items()
                                  if content["category"] in codes), 0)
        score = (4 * (content["category"] in explicit) + 2 * affinity + tag_affinity
                 + .5 * unheard + .3 * bool(h.get("saved")) + SELECTION_WEIGHT * selected_affinity)
        if not (h.get("liked") or h.get("saved")):
            score -= skips.get(content["content_id"], {}).get("penalty", 0)
        reasons = []
        for condition, reason in [(content["category"] in explicit, "explicit_topic"),
                                  (affinity > 0 or tag_affinity > 0, "positive_feedback"),
                                  (selected_affinity > 0, "past_topic_selection"),
                                  (unheard, "not_yet_heard"), (h.get("saved"), "saved")]:
            if condition:
                reasons.append(reason)
        candidates.append({**content, "recommendation_score": round(score, 6),
                           "reason_codes": reasons or ["catalog_order"]})
    candidates.sort(key=lambda c: ((0 if baseline else -c["recommendation_score"]), management_key(c)))
    if baseline:
        for c in candidates:
            c["reason_codes"] = ["catalog_order"]
        return candidates, available

    # Cover selected groups without letting their alphabetical order erase affinity.
    first_round = []
    for group in sorted(groups):
        candidate = next((c for c in candidates if c["category"] in GROUPS[group]), None)
        if candidate:
            first_round.append(candidate)
            candidates.remove(candidate)
    result = sorted(first_round, key=lambda c: (-c["recommendation_score"], management_key(c)))
    while candidates:
        index = choose_index(candidates, result)
        result.append(candidates.pop(index))
    return result, available
