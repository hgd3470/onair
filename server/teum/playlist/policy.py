"""Immutable, storage-independent S1-12 policy snapshot."""
from dataclasses import asdict, dataclass, fields
import math


@dataclass(frozen=True)
class PlaylistPolicy:
    policy_id: str = "default-v1"
    ad_min_ratio: float = 0.10
    ad_max_ratio: float = 0.15
    selected_group_min_ratio: float = 0.10

    def __post_init__(self):
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be a nonempty string")
        for name in ("ad_min_ratio", "ad_max_ratio", "selected_group_min_ratio"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if not 0 <= self.ad_min_ratio <= self.ad_max_ratio < 1:
            raise ValueError("require 0 <= ad_min_ratio <= ad_max_ratio < 1")
        if not 0 <= self.selected_group_min_ratio <= 0.2:
            raise ValueError("selected_group_min_ratio must be within [0, 0.2] for five groups")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) - {f.name for f in fields(cls)}:
            raise ValueError("unknown policy fields or invalid policy object")
        return cls(**data)
