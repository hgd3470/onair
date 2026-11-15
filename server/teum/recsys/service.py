"""추천 한 번의 흐름: 규칙 점수 → 전략 가산점 → 시간·광고 편성 → 노출 기록.

playlist/recommendation.recommend()와 같은 편성 함수(plan)와 같은 부족 사유 규칙을 쓴다.
recommend()에 전략 끼울 자리가 없어 그 조립 부분만 여기서 다시 한다. playlist 코드에
rerank 인자 하나가 생기면 이 중복은 지울 수 있다.
"""
import hashlib
import math
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from teum.playlist.planner import plan
from teum.playlist.ranker import ALGORITHM_VERSION, TAXONOMY_VERSION, rank_candidates
from teum.playlist.selection_preferences import PROFILE_VERSION
from teum.playlist.skip_preferences import PROFILE_VERSION as SKIP_PROFILE_VERSION
from teum.playlist.topics import GROUPS
from teum.recsys import ease, signals
from teum.recsys.context import context_bonuses
from teum.recsys.serve_log import ServeLog
from teum.recsys.strategies import STRATEGY_VERSION, reorder, rerank

STATS_TTL_SEC = 60


class RecsysService:
    def __init__(self, events_db, serve_db=None, model_path=None):
        self.events_db = Path(events_db)
        base = self.events_db.parent
        self.serve_log = ServeLog(serve_db or base / "recsys_serve.sqlite")
        self.model_path = Path(model_path or base / "recsys_ease.json")
        self._stats, self._stats_at = {}, -1e9
        self._model, self._model_mtime = None, None

    def stats(self):
        if time.monotonic() - self._stats_at > STATS_TTL_SEC:
            self._stats, self._stats_at = signals.content_stats(self.events_db), time.monotonic()
        return self._stats

    def model(self):
        try:
            mtime = self.model_path.stat().st_mtime if self.model_path.exists() else None
            if mtime != self._model_mtime:
                self._model, self._model_mtime = ease.load(self.model_path), mtime
        except (OSError, ValueError, KeyError):
            self._model, self._model_mtime = None, None  # 깨진 모델 파일이면 ease는 bandit으로 넘어간다
        return self._model

    def status(self):
        model = self.model()
        return {"strategy_version": STRATEGY_VERSION, "base_algorithm": ALGORITHM_VERSION,
                "data": signals.counts(self.events_db), "served_requests": self.serve_log.count(),
                "ease_model": None if not model else {k: model[k] for k in ("version", "users", "positives", "lam")}
                              | {"items": len(model["items"])}}


def seed_for(user_id, now):
    """같은 사용자는 한 시간 동안 같은 탐색 결과를 받는다 (새로고침마다 목록이 흔들리지 않게)."""
    key = f"{user_id}|{now.strftime('%Y-%m-%dT%H')}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)


def apply_context(ranked, groups, *, family=False, hour=None):
    """차량 맥락 가산점을 더하고 같은 다양성 규칙으로 순서를 다시 만든다. 쓸 맥락이 없으면 그대로."""
    bonuses = context_bonuses(ranked, family=family, hour=hour)
    if not bonuses:
        return ranked
    out = []
    for c in ranked:
        bonus, reasons = bonuses.get(c["content_id"], (0.0, []))
        reason_codes = list(c.get("reason_codes", [])) + [r for r in reasons if r not in c.get("reason_codes", [])]
        out.append({**c, "recommendation_score": round(c["recommendation_score"] + bonus, 6),
                    "context_bonus": round(bonus, 6), "reason_codes": reason_codes})
    return reorder(out, groups)


def compose(service, contents, minutes, history, *, user_id, strategy, interests=(), groups=(), speed=1.0,
            mode="recommend", policy=None, assignment="request", context=None, now=None, rng=None, log=True,
            selections=(), events=(), family=False, hour=None):
    now = now or datetime.now(timezone.utc)
    ranked, available = rank_candidates(contents, history, interests, groups, mode, False, selections, now, events)
    liked = [cid for cid, h in history.items() if h.get("completed") or h.get("liked") or h.get("saved")]
    rng = rng or random.Random(seed_for(user_id, now))
    ranked, used, fallback = rerank(strategy, ranked, groups, stats=service.stats(), rng=rng,
                                    model=service.model(), liked=liked)
    ranked = apply_context(ranked, groups, family=family, hour=hour)
    ads = [c for c in contents if c.get("kind") == "ad"
           and isinstance(c.get("duration_sec"), (int, float))
           and math.isfinite(c["duration_sec"]) and c["duration_sec"] > 0 and c.get("audio_uri")]
    quota_groups = {g for g, codes in GROUPS.items() if codes.intersection(interests)}
    response = plan(ranked, ads, minutes, speed=speed, mode=mode, groups=quota_groups, policy=policy)
    stories = [c for c in response["items"] if c["kind"] == "story"]
    if not available:
        code = "no_matching_topic" if mode == "pick" else "no_available_content"
    elif not ranked:
        code = "all_consumed"
    elif not stories:
        code = "no_item_fits_budget"
    elif (sum(c["duration_sec"] / speed for c in ranked)
          + sum(c["duration_sec"] for c in response["items"] if c["kind"] == "ad") < minutes * 60 - 1e-6):
        code = "partial_catalog"
    else:
        code = None
    response.update(algorithm_version=f"{ALGORITHM_VERSION}+{STRATEGY_VERSION}:{used}",
                    selection_profile_version=PROFILE_VERSION, skip_profile_version=SKIP_PROFILE_VERSION,
                    taxonomy_version=TAXONOMY_VERSION,
                    rule="personalized stories; strategy bonus; selected-group quotas; bounded ads; story ending",
                    packing={"remaining_sec": response["leftover_sec"],
                             "reason": "item_does_not_fit" if ranked and len(stories) < len(ranked)
                             and response["leftover_sec"] > 0 else None},
                    shortage={"code": code, "requested_sec": response["budget_sec"],
                              "available_sec": response["total_sec"], "missing_sec": response["leftover_sec"]})
    request_id, log_error = None, None
    if log:
        try:
            request_id = service.serve_log.write(user_id, used, strategy, fallback, assignment, STRATEGY_VERSION,
                                                 minutes, context or {}, response["items"])
        except (OSError, sqlite3.Error) as exc:  # 기록이 실패해도 목록은 내준다
            log_error = type(exc).__name__
    response["recsys"] = {"algorithm": used, "requested": strategy, "fallback": fallback,
                          "assignment": assignment, "strategy_version": STRATEGY_VERSION,
                          "request_id": request_id, "log_error": log_error, "context": context or {}}
    return response
