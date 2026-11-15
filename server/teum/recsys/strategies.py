"""추천 전략. 규칙 기반 점수(recommendation_score)에 가산점을 더해 순서를 다시 정한다.

- rule: 가산점 0. 규칙 추천과 같은 순서 (기준선).
- bandit: Thompson sampling. 콘텐츠마다 Beta(성공, 실패) 사후분포에서 하나 뽑아 가산점으로 쓴다.
  기록이 적은 콘텐츠는 분포가 넓어 가끔 위로 올라온다(탐색). 기록이 쌓이면 잘 듣는 콘텐츠가 올라온다(활용).
  새 콘텐츠는 같은 카테고리 평균을 출발점으로 쓴다.
- ease: 학습된 EASE 모델이 있고 데이터가 기준을 넘으면 "이걸 좋아한 사람은 이것도" 점수를 더한다.
  준비가 안 되면 bandit으로 넘어가고 응답에 fallback을 적는다.

가산점을 더한 뒤에도 규칙 추천의 다양성 조건(연속 5개 중 같은 대분류 최대 3개, 같은 카테고리 연속 금지)과
선택 대분류 첫 자리 보장은 같은 함수(choose_index)로 다시 적용한다.
"""
import random
from collections import defaultdict

from teum.playlist.diversity import choose_index
from teum.playlist.ranker import management_key
from teum.playlist.topics import GROUPS
from teum.recsys import ease

STRATEGY_VERSION = "recsys-v1"
NAMES = ("rule", "bandit", "ease")


def reorder(candidates, groups):
    """ranker.rank_candidates 끝부분과 같은 규칙으로 순서를 다시 만든다."""
    pending = sorted(candidates, key=lambda c: (-c["recommendation_score"], management_key(c)))
    first_round = []
    for group in sorted(groups):
        pick = next((c for c in pending if c["category"] in GROUPS[group]), None)
        if pick:
            first_round.append(pick)
            pending.remove(pick)
    result = sorted(first_round, key=lambda c: (-c["recommendation_score"], management_key(c)))
    while pending:
        result.append(pending.pop(choose_index(pending, result)))
    return result


def _apply(ranked, groups, bonuses, details):
    out = []
    for c in ranked:
        bonus = bonuses.get(c["content_id"], 0.0)
        rule = c["recommendation_score"]
        out.append({**c, "rule_score": rule, "recommendation_score": round(rule + bonus, 6),
                    "recsys": {"bonus": round(bonus, 6), **details.get(c["content_id"], {})}})
    return reorder(out, groups)


PRIOR_STRENGTH = 4  # 카테고리 성공률을 "가상 관측 4번"만큼만 믿는다


def bandit_bonuses(ranked, stats, rng, weight=1.0):
    """bonus = weight × (θ − 0.5) × 2, θ ~ Beta(α+성공, β+실패).

    α, β = 1 + k×(같은 카테고리 다른 콘텐츠의 성공률), 1 + k×(실패율). k = PRIOR_STRENGTH.
    자기 기록은 출발값에 넣지 않는다(두 번 세지 않게). 카테고리에 기록이 없으면 Beta(1, 1).
    """
    cat_sum = defaultdict(lambda: [0, 0])
    for c in ranked:
        s, f = stats.get(c["content_id"], (0, 0))
        cat_sum[c["category"]][0] += s
        cat_sum[c["category"]][1] += f
    bonuses, details = {}, {}
    for c in ranked:
        s, f = stats.get(c["content_id"], (0, 0))
        cs, cf = cat_sum[c["category"]][0] - s, cat_sum[c["category"]][1] - f
        rate = cs / (cs + cf) if cs + cf else 0.5
        k = PRIOR_STRENGTH if cs + cf else 0
        a, b = 1 + k * rate + s, 1 + k * (1 - rate) + f
        theta = rng.betavariate(a, b)
        bonuses[c["content_id"]] = weight * (theta - 0.5) * 2
        details[c["content_id"]] = {"posterior_mean": round(a / (a + b), 4), "evidence": s + f,
                                    "sample": round(theta, 4)}
    return bonuses, details


def ease_bonuses(ranked, model, liked, weight=1.5):
    raw = ease.scores(model, liked)
    for cid in liked:  # 이미 좋아한 콘텐츠는 자기 이웃 덕에 다시 오르지 않게 뺀다
        raw.pop(cid, None)
    top = max((v for v in raw.values() if v > 0), default=0)
    bonuses, details = {}, {}
    for c in ranked:
        v = raw.get(c["content_id"], 0.0)
        norm = v / top if top > 0 and v > 0 else 0.0
        bonuses[c["content_id"]] = weight * norm
        details[c["content_id"]] = {"ease_score": round(v, 6)}
    return bonuses, details


def ease_ready(model, min_users=20, min_positives=100):
    return bool(model) and model.get("users", 0) >= min_users and model.get("positives", 0) >= min_positives


def rerank(name, ranked, groups, *, stats, rng=None, model=None, liked=(), bandit_weight=1.0, ease_weight=1.5):
    """반환: (다시 정렬한 후보, 실제로 쓴 전략 이름, 넘어간 이유 또는 None)."""
    if name not in NAMES:
        raise ValueError(f"unknown strategy: {name}")
    if name == "rule":
        return _apply(ranked, groups, {}, {}), "rule", None
    if name == "ease":
        known = [c for c in liked if model and c in set(model.get("items", ()))]
        if ease_ready(model) and known:
            bonuses, details = ease_bonuses(ranked, model, known, ease_weight)
            return _apply(ranked, groups, bonuses, details), "ease", None
        if not ease_ready(model):
            reason = "ease_model_not_ready"
        elif not liked:
            reason = "no_positive_history"
        else:
            reason = "no_known_positive_in_model"
        bonuses, details = bandit_bonuses(ranked, stats, rng or random.Random(0), bandit_weight)
        return _apply(ranked, groups, bonuses, details), "bandit", reason
    bonuses, details = bandit_bonuses(ranked, stats, rng or random.Random(0), bandit_weight)
    return _apply(ranked, groups, bonuses, details), "bandit", None
