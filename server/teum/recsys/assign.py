"""어떤 사용자에게 어떤 전략을 쓸지 정한다 (비교 실험용 나눔).

우선순위: 요청의 algo 값 > TEUM_RECSYS_SPLIT 비율 나눔 > TEUM_RECSYS_DEFAULT(기본 rule).
TEUM_RECSYS_SPLIT 예: "rule:50,bandit:50". 같은 사용자는 항상 같은 쪽에 떨어진다(user_id 해시).
"""
import hashlib
import math
import os

from teum.recsys.strategies import NAMES


def parse_split(text):
    parts = []
    for chunk in (text or "").split(","):
        if not chunk.strip():
            continue
        name, _, share = chunk.partition(":")
        name = name.strip()
        if name not in NAMES:
            raise ValueError(f"unknown strategy in split: {name}")
        value = float(share or 0)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid share for {name}: {share}")
        parts.append((name, value))
    total = sum(s for _, s in parts)
    if parts and total <= 0:
        raise ValueError("split shares must be positive")
    return [(n, s / total) for n, s in parts]


def bucket(user_id):
    return int(hashlib.sha256(user_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def choose(user_id, requested=None, env=None):
    env = os.environ if env is None else env
    if requested:
        return requested, "request"
    split = parse_split(env.get("TEUM_RECSYS_SPLIT", ""))
    if split:
        x, acc = bucket(user_id), 0.0
        for name, share in split:
            acc += share
            if x <= acc:
                return name, "split"
        return split[-1][0], "split"
    default = env.get("TEUM_RECSYS_DEFAULT", "rule")
    if default not in NAMES:
        raise ValueError(f"unknown TEUM_RECSYS_DEFAULT: {default}")
    return default, "default"
