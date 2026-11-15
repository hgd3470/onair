"""Replace this read boundary with an admin-owned DB provider in the future."""
import json
import os
from pathlib import Path

from teum.playlist.policy import PlaylistPolicy


def load_policy():
    path = os.environ.get("TEUM_PLAYLIST_POLICY_FILE")
    if not path:
        return PlaylistPolicy()
    # A fresh, validated snapshot per request. Operators must replace atomically.
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate policy field")
            result[key] = value
        return result
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique_keys)
    return PlaylistPolicy.from_dict(data)


def get_policy_provider():
    return load_policy
