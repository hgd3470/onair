"""Compose S1-11 personalization and S1-12 finite policy-based planning."""
import math

from teum.playlist.ranker import ALGORITHM_VERSION, TAXONOMY_VERSION, rank_candidates
from teum.playlist.planner import plan
from teum.playlist.topics import GROUPS
from teum.playlist.selection_preferences import PROFILE_VERSION
from teum.playlist.skip_preferences import PROFILE_VERSION as SKIP_PROFILE_VERSION


def recommend(contents, minutes, history, interests=(), groups=(), speed=1.0,
              mode="recommend", baseline=False, selections=(), now=None, events=(), policy=None):
    if mode not in {"recommend", "pick"}:
        raise ValueError("unsupported mode")
    if not math.isfinite(minutes) or not 0 < minutes <= 600:
        raise ValueError("minutes must be finite and within (0, 600]")
    if not math.isfinite(speed) or not 0 < speed <= 3:
        raise ValueError("speed must be finite and within (0, 3]")
    interests, groups = tuple(interests), tuple(groups)
    if set(groups) - GROUPS.keys():
        raise ValueError("unknown topic group")
    ranked, available = rank_candidates(contents, history, interests, groups, mode, baseline, selections, now, events)
    ads = [c for c in contents if c.get("kind") == "ad"
           and isinstance(c.get("duration_sec"), (int, float))
           and math.isfinite(c["duration_sec"]) and c["duration_sec"] > 0
           and c.get("audio_uri")]
    quota_groups = {g for g, codes in GROUPS.items() if codes.intersection(interests)}
    response = plan(ranked, ads, minutes, speed=speed, mode=mode, groups=quota_groups,
                    policy=policy, baseline=baseline)
    stories = [c for c in response["items"] if c["kind"] == "story"]
    if not available:
        code = "no_matching_topic" if mode == "pick" else "no_available_content"
    elif not ranked:
        code = "all_consumed"
    elif not stories:
        code = "no_item_fits_budget"
    elif (sum(c["duration_sec"] / speed for c in ranked)
          + sum(c["duration_sec"] for c in response["items"] if c["kind"] == "ad")
          < minutes * 60 - 1e-6):
        code = "partial_catalog"
    else:
        code = None
    response.update(algorithm_version="b0-fixed-v1" if baseline else ALGORITHM_VERSION,
                    selection_profile_version=PROFILE_VERSION,
                    skip_profile_version=SKIP_PROFILE_VERSION,
                    taxonomy_version=TAXONOMY_VERSION,
                    rule="fixed catalog order; policy planning" if baseline else "personalized stories; selected-group quotas; bounded ads; story ending",
                    packing={"remaining_sec": response["leftover_sec"],
                             "reason": "item_does_not_fit" if ranked and len(stories) < len(ranked)
                             and response["leftover_sec"] > 0 else None},
                    shortage={"code": code, "requested_sec": response["budget_sec"],
                              "available_sec": response["total_sec"],
                              "missing_sec": response["leftover_sec"]})
    return response
