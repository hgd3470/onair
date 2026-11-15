"""Stateless story suggestions; callers own playback, reservations and ads."""
from teum.playlist.ranker import rank_candidates
from teum.playlist.diversity import choose_index


def next_candidates(contents, history, *, current_id=None, queued_ids=(),
                    consumed_ids=(), target_size=5, interests=(), groups=(),
                    mode="recommend", selections=(), events=(), now=None,
                    remaining_sec=None, speed=1.0):
    """Return only new stories to append, without changing existing reservations.

    queued_ids includes all pending queue slots (including ads if present).
    remaining_sec is the caller's unreserved time, after current/queued items.
    This function neither inserts ads nor persists state or listening evidence.
    """
    import math
    if not isinstance(target_size, int) or not 0 <= target_size <= 5:
        raise ValueError("target_size must be an integer from 0 to 5")
    if not math.isfinite(speed) or not 0 < speed <= 3:
        raise ValueError("speed must be finite and within (0, 3]")
    if remaining_sec is not None and (not math.isfinite(remaining_sec) or remaining_sec < 0):
        raise ValueError("remaining_sec must be finite and nonnegative")
    if mode not in {"recommend", "pick"}:
        raise ValueError("unsupported mode")
    queued_ids = tuple(queued_ids)
    excluded = set(queued_ids) | set(consumed_ids) | {current_id}
    ranked, _ = rank_candidates(contents, history, interests, groups, mode,
                                selections=selections, events=events, now=now)
    slots = max(0, target_size - len(queued_ids))
    remaining = math.inf if remaining_sec is None else remaining_sec
    additions = []
    by_id = {c["content_id"]: c for c in contents}
    prefix = [by_id.get(cid, {}) for cid in queued_ids]
    while len(additions) < slots:
        candidates = [c for c in ranked if c["content_id"] not in excluded
                      and c["duration_sec"] / speed <= remaining]
        if not candidates:
            break
        item = candidates[choose_index(candidates, prefix + additions)]
        duration = item["duration_sec"] / speed
        additions.append(item)
        excluded.add(item["content_id"])
        remaining -= duration
    return additions
