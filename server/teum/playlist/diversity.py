"""Pure queue ordering: at most three of a group in a rolling five slots.

Relax only when no eligible alternative remains. Callers filter topic, exclusion
and time constraints first. Ads/unknown categories occupy slots but no A-E count.
"""
from collections import Counter
from teum.playlist.topics import GROUPS


def group_of(item):
    if item.get("kind") != "story":
        return None
    return next((g for g, codes in GROUPS.items() if item.get("category") in codes), None)


def choose_index(candidates, prefix):
    counts = Counter(group_of(item) for item in prefix[-4:])
    allowed = [i for i, c in enumerate(candidates)
               if group_of(c) is None or counts[group_of(c)] < 3]
    # Supply shortage or a strict one-group pick must not invent outside topics.
    if not allowed:
        allowed = list(range(len(candidates)))
    if len(prefix) >= 2 and prefix[-1].get("kind") == prefix[-2].get("kind") == "story":
        category = prefix[-1].get("category")
        if category == prefix[-2].get("category"):
            return next((i for i in allowed if candidates[i].get("category") != category), allowed[0])
    return allowed[0]
