"""S1-12 finite planning. No HTTP, playback state, DB, or policy-file access.

Times use conservative 0.1-second ticks. Ads always play at 1x. Whole stories
are unique; ads may repeat between stories. Bounded deterministic candidate
search is a heuristic, not a proof of globally optimal knapsack packing.
"""
from collections import Counter
from math import ceil, floor, gcd, isfinite
from functools import reduce

from teum.playlist.diversity import choose_index, group_of
from teum.playlist.policy import PlaylistPolicy
from teum.playlist.ranker import management_key

PLANNER_VERSION = "s1-12-quota-ads-v1"
MAX_AD_PLANS = 256


def ticks(seconds):
    return ceil(seconds * 10 - 1e-8)


def _pack(stories, capacity, groups, ratio, diversify, shortest_first=False):
    pending = list(stories)
    chosen = []
    remaining = capacity
    target = min(capacity, sum(c["_ticks"] for c in stories)) * ratio
    # Reserve scarce groups first, then spend the rest on personalized stories.
    ordered_groups = sorted(groups, key=lambda g: (
        sum(c["_ticks"] for c in stories if group_of(c) == g), g))
    for group in ordered_groups:
        allocated = 0
        while allocated < target - 1e-8 or (allocated == 0 and ratio > 0):
            options = [c for c in pending if group_of(c) == group and c["_ticks"] <= remaining]
            if not options:
                break
            if shortest_first:
                options.sort(key=lambda c: (c["_ticks"], -c.get("recommendation_score", 0), management_key(c)))
            item = options[0]
            chosen.append(item)
            pending.remove(item)
            allocated += item["_ticks"]
            remaining -= item["_ticks"]
    while True:
        options = [c for c in pending if c["_ticks"] <= remaining]
        if not options:
            break
        item = options[choose_index(options, chosen) if diversify else 0]
        chosen.append(item)
        pending.remove(item)
        remaining -= item["_ticks"]
    # Reservations affect membership, not a mandatory A/B/C prefix on playback.
    order = {c["content_id"]: i for i, c in enumerate(stories)}
    chosen.sort(key=lambda c: order[c["content_id"]])
    if diversify:
        result = []
        while chosen:
            result.append(chosen.pop(choose_index(chosen, result)))
        return result
    return chosen


def _ad_options(ads, ceiling, slots):
    """Reachable durations and minimum slot counts via unbounded coin DP."""
    if not ads or ceiling <= 0 or slots <= 0:
        return {0: (0, None)}, 1
    unit = reduce(gcd, (a["_ticks"] for a in ads))
    coins = sorted(set(a["_ticks"] // unit for a in ads))
    limit = ceiling // unit
    dp = {0: (0, None)}
    for total in range(1, limit + 1):
        best = None
        for coin in coins:
            previous = dp.get(total - coin)
            if previous is not None and previous[0] < slots:
                candidate = (previous[0] + 1, coin)
                if best is None or candidate < best:
                    best = candidate
        if best is not None:
            dp[total] = best
    return dp, unit


def _ad_sequence(dp, unit, total, ads, existing=()):
    durations = []
    while total:
        coin = dp[total][1]
        durations.append(coin * unit)
        total -= coin
    used = Counter(a["content_id"] for a in existing)
    result = []
    for duration in durations:
        matching = [a for a in ads if a["_ticks"] == duration]
        item = min(matching, key=lambda a: (used[a["content_id"]], a["content_id"]))
        used[item["content_id"]] += 1
        result.append(item)
    return result


def _unique_ad_options(ads, ceiling, slots):
    """0/1 duration DP: each creative at most once before repeat fallback."""
    options = {0: []}
    for ad in ads:
        for total, sequence in list(options.items()):
            new_total = total + ad["_ticks"]
            if new_total > ceiling or len(sequence) >= slots:
                continue
            candidate = sequence + [ad]
            if new_total not in options or len(candidate) < len(options[new_total]):
                options[new_total] = candidate
    return options


def _ad_variants(total, dp, unit, ads, unique_options):
    # Start with distinct creatives, then fill only the missing duration. Keep
    # a candidate per slot count so diversity does not hide a fitting plan.
    variants = {}
    for base_ticks, base in unique_options.items():
        remaining = total - base_ticks // unit
        if remaining not in dp:
            continue
        sequence = base + _ad_sequence(dp, unit, remaining, ads, base)
        repeats = len(sequence) - len({a["content_id"] for a in sequence})
        old = variants.get(len(sequence))
        if old is None or repeats < old[0]:
            variants[len(sequence)] = (repeats, sequence)
    return [v[1] for v in variants.values()]


def _rotate_ads(sequence):
    # Show each chosen creative once before a second round, without user data.
    counts = Counter(a["content_id"] for a in sequence)
    by_id = {a["content_id"]: a for a in sequence}
    result = []
    while any(counts.values()):
        for cid in sorted(counts):
            if counts[cid]:
                result.append(by_id[cid])
                counts[cid] -= 1
    return result


def _quota(stories, groups, ratio):
    total = sum(c["_ticks"] for c in stories)
    actual = Counter()
    for c in stories:
        actual[group_of(c)] += c["_ticks"]
    covered = sum(actual[g] > 0 for g in groups)
    met = all(actual[g] > 0 and actual[g] + 1e-8 >= ratio * total for g in groups) if ratio else True
    return total, actual, covered, met


def plan(ranked, ads, minutes, *, speed=1.0, mode="recommend", groups=(),
         policy=None, baseline=False):
    policy = policy or PlaylistPolicy()
    if not isfinite(minutes) or not 0 < minutes <= 600:
        raise ValueError("minutes must be finite and within (0, 600]")
    if not isfinite(speed) or not 0 < speed <= 3:
        raise ValueError("speed must be finite and within (0, 3]")
    budget = floor(minutes * 600 + 1e-8)
    groups = sorted(set(groups)) if mode == "pick" and not baseline else []
    # Pure rank API retains its rolling-five behavior. Finite pick uses quotas.
    if mode == "pick" and not baseline:
        ranked = sorted(ranked, key=lambda c: (-c["recommendation_score"], management_key(c)))
    stories = [{**c, "_ticks": ticks(c["duration_sec"] / speed)} for c in ranked]
    stories = [c for c in stories if c["_ticks"] <= budget]
    ads = sorted(({**c, "_ticks": ticks(c["duration_sec"])} for c in ads), key=lambda c: c["content_id"])
    ceiling, slots = floor(budget * policy.ad_max_ratio), max(0, len(stories) - 1)
    dp, unit = _ad_options(ads, ceiling, slots)
    unique_options = _unique_ad_options(ads, ceiling, slots)
    totals = sorted(dp)
    limited = len(totals) > MAX_AD_PLANS
    if limited:
        # Keep coverage of the whole range, plus the nominal full-budget floor.
        indices = {round(i * (len(totals) - 1) / (MAX_AD_PLANS - 2)) for i in range(MAX_AD_PLANS - 1)}
        indices.add(min(range(len(totals)), key=lambda i: abs(totals[i] * unit - budget * policy.ad_min_ratio)))
        totals = [totals[i] for i in sorted(indices)]
    best, selected, chosen_ads = None, [], []
    for ad_units in totals:
        ad_total = ad_units * unit
        sequences = _ad_variants(ad_units, dp, unit, ads, unique_options)
        for sequence in sequences:
            repeats = len(sequence) - len({a["content_id"] for a in sequence})
            for shortest in ([False, True] if groups else [False]):
                packed = _pack(stories, budget - ad_total, groups, policy.selected_group_min_ratio,
                               mode == "recommend" and not baseline, shortest)
                story_total, actual, covered, quota_met = _quota(packed, groups, policy.selected_group_min_ratio)
                total = story_total + ad_total
                if ad_total and (len(sequence) > len(packed) - 1 or ad_total > total * policy.ad_max_ratio + 1e-8):
                    continue
                ad_met = bool(total) and ad_total + 1e-8 >= total * policy.ad_min_ratio
                # Preserve topic representation, meet floors, then avoid repeats.
                quality = (covered if policy.selected_group_min_ratio else 0,
                           quota_met, ad_met, -repeats, story_total,
                           len(sequence) - repeats, -ad_total)
                if best is None or quality > best:
                    best, selected, chosen_ads = quality, packed, sequence
    story_total, group_times, _, _ = _quota(selected, groups, policy.selected_group_min_ratio)
    chosen_ads = _rotate_ads(chosen_ads)
    ad_total = sum(c["_ticks"] for c in chosen_ads)
    total = story_total + ad_total
    # Distribute ads among distinct story boundaries, near equal airtime points.
    gaps, elapsed = {}, 0
    for i, story in enumerate(selected[:-1], 1):
        elapsed += story["_ticks"]
        gaps[i] = elapsed
    placements = {}
    for i, ad in enumerate(chosen_ads, 1):
        target = story_total * i / (len(chosen_ads) + 1)
        gap = min(gaps, key=lambda g: (abs(gaps[g] - target), g))
        placements[gap] = ad
        del gaps[gap]
    placements = dict(zip(sorted(placements), chosen_ads))
    sequence = []
    for i, story in enumerate(selected, 1):
        sequence.append(story)
        if i in placements:
            sequence.append(placements[i])
    items, start = [], 0
    for c in sequence:
        item = {k: c.get(k) for k in ("content_id", "kind", "title", "category", "audio_uri")}
        item.update(duration_sec=c["_ticks"] / 10, start_at_sec=start / 10,
                    playback_rate=speed if c["kind"] == "story" else 1.0)
        if c["kind"] == "story":
            item.update(attribution=c.get("attribution"), license=c.get("license"),
                        reason_codes=c["reason_codes"], reason_text=_reason_text(c["reason_codes"]))
        items.append(item)
        start += c["_ticks"]
    ad_met = bool(total) and ad_total + 1e-8 >= total * policy.ad_min_ratio
    if not total:
        ad_reason = "no_story_planned"
    elif ad_met:
        ad_reason = None
    elif not ads:
        ad_reason = "no_eligible_ads"
    else:
        ad_reason = "no_compliant_ad_plan_found"
    quotas = []
    for group in groups:
        actual = group_times[group]
        met = (actual > 0 and actual + 1e-8 >= story_total * policy.selected_group_min_ratio) or policy.selected_group_min_ratio == 0
        supply = sum(c["_ticks"] for c in stories if group_of(c) == group)
        quotas.append({"group_id": group, "planned_sec": actual / 10,
                       "actual_ratio": actual / story_total if story_total else 0,
                       "required_sec": round(story_total * policy.selected_group_min_ratio / 10, 4),
                       "minimum_met": met,
                       "reason": None if met else ("no_fitting_group_content" if not supply else "quota_plan_not_found")})
    return {"items": items, "total_sec": total / 10, "budget_sec": budget / 10,
            "leftover_sec": (budget - total) / 10, "planner_version": PLANNER_VERSION,
            "policy": policy.to_dict(),
            "advertising": {"planned_sec": ad_total / 10, "story_sec": story_total / 10,
                            "unique_creatives": len({a["content_id"] for a in chosen_ads}),
                            "repeat_count": len(chosen_ads) - len({a["content_id"] for a in chosen_ads}),
                            "actual_ratio": ad_total / total if total else 0,
                            "required_min_sec": round(total * policy.ad_min_ratio / 10, 4),
                            "allowed_max_sec": round(total * policy.ad_max_ratio / 10, 4),
                            "minimum_met": ad_met, "reason": ad_reason},
            "topic_quotas": quotas,
            "planning": {"strategy": "bounded_greedy_reservations", "ad_search_limited": limited,
                         "ad_plans_evaluated": len(totals)}}


def _reason_text(codes):
    labels = {"explicit_topic": "선택한 관심 주제", "positive_feedback": "긍정 반응과 관련된 이야기",
              "past_topic_selection": "이전에 골라 들은 주제", "not_yet_heard": "아직 듣지 않은 이야기",
              "saved": "저장한 이야기", "catalog_order": "기본 순서"}
    return " · ".join(labels.get(code, code) for code in codes)
