"""Pure group preference calculation; callers supply selection records."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from teum.playlist.topics import GROUPS

WINDOW_DAYS = 30
HALF_LIFE_DAYS = 14
PROFILE_VERSION = "selection-group-v1"
SELECTION_WEIGHT = 0.3


@dataclass(frozen=True)
class TopicSelection:
    selection_id: str
    selected_at: datetime
    group_ids: tuple[str, ...]
    playback_started: bool


def group_preferences(selections: Sequence[TopicSelection], *, now: datetime) -> dict[str, float]:
    """30-day window, 14-day half-life, at most one total vote per UTC day.

    Split each vote across distinct selected groups. Conflicting copies of an ID
    are ignored rather than letting arrival order change the profile. No storage,
    current-interest mutation, or religious-content eligibility inference occurs.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    now = now.astimezone(timezone.utc)
    unique, conflicting = {}, set()
    for row in selections:
        if (not row.selection_id or not row.playback_started or not row.group_ids
                or set(row.group_ids) - GROUPS.keys()
                or row.selected_at.tzinfo is None or row.selected_at.utcoffset() is None):
            continue
        stamp = row.selected_at.astimezone(timezone.utc)
        if not now - timedelta(days=WINDOW_DAYS) <= stamp <= now:
            continue
        normalized = (stamp, tuple(sorted(set(row.group_ids))))
        if row.selection_id in unique and unique[row.selection_id] != normalized:
            conflicting.add(row.selection_id)
        unique[row.selection_id] = normalized
    days = defaultdict(list)
    for sid, (stamp, groups) in sorted(unique.items()):
        if sid not in conflicting:
            days[stamp.date()].append((stamp, groups))
    scores, total = defaultdict(float), 0.0
    for _, rows in sorted(days.items()):
        # Cap before decay, so many old records cannot recover a full fresh vote.
        for stamp, groups in rows:
            age = (now - stamp).total_seconds() / 86400
            weight = 2 ** (-age / HALF_LIFE_DAYS) / len(rows)
            total += weight
            for group in groups:
                scores[group] += weight / len(groups)
    return {group: scores[group] / (3 + total) for group in sorted(scores)}
