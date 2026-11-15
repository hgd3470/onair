"""추천 틀 HTTP. 기존 GET /playlist는 그대로 두고 옆에 붙인다.

GET /recsys/playlist: /playlist와 같은 입력 + algo(rule·bandit·ease) + 차량 맥락(remain_sec 등).
GET /recsys/status: 쓸 수 있는 전략, 데이터 양, EASE 모델 상태.

차량 맥락 값은 Pleos SDK에서 앱이 읽어 보낸다. remain_sec = NaviHelper getDestinationInfo().remainTime(초).
minutes가 없으면 remain_sec로 편성 시간을 정한다.
family(뒷좌석 탑승 후 운전자 동의)와 hour(출근 시간대)는 recsys/context.py의 작은 가산점으로 순서에 반영한다.
passengers는 아이 여부를 알 수 없어 기록만 한다.
"""
from typing import Annotated, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from teum.core.deps import CatalogDep, ListeningDep
from teum.playlist.policy import PlaylistPolicy
from teum.playlist.policy_provider import get_policy_provider
from teum.playlist.topics import CATEGORY_CODES, GROUPS
from teum.recsys import assign
from teum.recsys.context import used_context
from teum.recsys.service import RecsysService, compose

router = APIRouter(tags=["recsys"])


def get_recsys(request: Request) -> RecsysService:
    service = getattr(request.app.state, "recsys", None)
    if service is None:
        service = request.app.state.recsys = RecsysService(request.app.state.settings.events_db)
    return service


RecsysDep = Annotated[RecsysService, Depends(get_recsys)]


@router.get("/recsys/playlist")
def recsys_playlist(user_id: str, catalog: CatalogDep, listening: ListeningDep, service: RecsysDep,
                    policy_provider: Annotated[Callable[[], PlaylistPolicy], Depends(get_policy_provider)],
                    minutes: float | None = Query(None, gt=0, le=600, allow_inf_nan=False),
                    remain_sec: float | None = Query(None, gt=0, le=36000, allow_inf_nan=False),
                    interests: str = "", speed: float = Query(1.0, gt=0, le=3, allow_inf_nan=False),
                    mode: Literal["recommend", "pick"] = "recommend",
                    algo: Literal["rule", "bandit", "ease"] | None = None,
                    passengers: int | None = Query(None, ge=0, le=9), hour: int | None = Query(None, ge=0, le=23),
                    family: bool = False, log: bool = True):
    if not user_id.strip():
        raise HTTPException(422, "user_id is required")
    if minutes is None:
        if remain_sec is None:
            raise HTTPException(422, "minutes or remain_sec is required")
        minutes = min(600.0, remain_sec / 60)
    ints = list(dict.fromkeys(x.strip() for x in interests.split(",") if x.strip()))
    context = {k: v for k, v in {"remain_sec": remain_sec, "passengers": passengers, "hour": hour,
                                 "family": family or None}.items() if v is not None}
    return run_recsys(user_id, minutes, catalog=catalog, listening=listening, service=service,
                      policy_provider=policy_provider, interests=ints, speed=speed, mode=mode, algo=algo,
                      context=context, family=family, hour=hour, log=log)


def run_recsys(user_id, minutes, *, catalog, listening, service, policy_provider, interests=(), speed=1.0,
               mode="recommend", algo=None, context=None, family=False, hour=None, log=True):
    """/recsys/playlist 본체. 루틴(routine/router.py)의 '남은 시간만큼 추천' 단계도 이 함수를 쓴다."""
    ints = list(interests) or listening.interests(user_id)
    valid = CATEGORY_CODES | {c["category"] for c in catalog.stories}
    if set(ints) - valid:
        raise HTTPException(422, "unknown interest category; send expanded C codes")
    if mode == "pick" and not ints:
        raise HTTPException(422, "pick requires interests")
    try:
        strategy, assignment = assign.choose(user_id, algo)
    except ValueError as exc:
        raise HTTPException(503, f"recsys configuration error: {exc}") from exc
    groups = {g for g, codes in GROUPS.items() if codes.intersection(ints)}
    playable = [c for c in catalog.items
                if (path := catalog.audio_path(c["audio_uri"])) is not None and path.is_file()]
    try:
        policy = policy_provider()
        if not isinstance(policy, PlaylistPolicy):
            raise ValueError("provider must return PlaylistPolicy")
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(503, "playlist policy unavailable") from exc
    context = context or {}
    response = compose(service, playable, minutes, listening.history(user_id, catalog), user_id=user_id,
                       strategy=strategy, interests=ints, groups=groups, speed=speed, mode=mode, policy=policy,
                       assignment=assignment, context=context, log=log, selections=listening.selections(user_id),
                       events=listening.playback_events(user_id), family=family, hour=hour)
    response["signal_status"] = signal_status(strategy, context, family, hour)
    return response


def signal_status(strategy, context, family=False, hour=None):
    return {
        "interests": "connected", "reactions_and_listening": "connected",
        "population_feedback": "connected" if strategy != "rule" else "unused",
        "vehicle_context": "connected" if used_context(family, hour) else ("logged_only" if context else "not_sent"),
        "selection_history": "connected", "skip_timing": "connected",
    }


@router.get("/recsys/status")
def recsys_status(service: RecsysDep):
    return {**service.status(), "strategies": ["rule", "bandit", "ease"],
            "default": assign.os.environ.get("TEUM_RECSYS_DEFAULT", "rule"),
            "split": assign.os.environ.get("TEUM_RECSYS_SPLIT", "")}
