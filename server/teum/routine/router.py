"""루틴 (S2-12). 브리핑을 정한 순서대로 잇고, 마지막은 남은 시간만큼 추천 이야기로 채운다.

예) 출근길 루틴: 날씨 → 경제 브리핑 → 교통 → 추천 콘텐츠(도착까지 남은 시간)
- GET/PUT /me/routines: 사용자별 루틴 저장 (기기 토큰). 저장한 적이 없으면 기본 '출근길 루틴'을 준다.
- POST /routines/{routine_id}/plan: 지금 상황으로 재생 순서를 만든다. 브리핑은 기기 TTS로 읽을 문장(text),
  추천 단계는 /recsys/playlist와 같은 이야기·광고 목록이다. 정보를 못 얻은 브리핑(위치 없음, 경로 안내 없음)은
  건너뛰고 skipped에 이유를 적는다.
도착이 가까워지면 듣던 편까지만 재생하고 루틴을 끝내는 것은 앱(NaviHelper onDestinationArrived)이 맡는다.
"""
import re
from typing import Annotated, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from teum.briefing.router import Situation, get_briefing
from teum.core.deps import CatalogDep, CurrentUserDep, ListeningDep
from teum.playlist.policy import PlaylistPolicy
from teum.playlist.policy_provider import get_policy_provider
from teum.recsys.router import RecsysDep, run_recsys

router = APIRouter(tags=["routine"])
StepType = Literal["weather", "economy", "traffic", "recommend"]
STEP_TITLES = {"weather": "오늘 날씨", "economy": "경제 브리핑", "traffic": "고속도로 교통상황", "recommend": "추천 콘텐츠"}
DEFAULT_ROUTINES = [{"routine_id": "commute", "name": "출근길 루틴", "label": "평일 오전",
                     "steps": [{"type": t} for t in ("weather", "economy", "traffic", "recommend")]}]
MIN_RECOMMEND_SEC = 60


class Step(BaseModel):
    type: StepType


class Routine(BaseModel):
    routine_id: str
    name: str = Field(min_length=1, max_length=20)
    label: str | None = Field(None, max_length=20)
    steps: list[Step] = Field(min_length=1, max_length=8)

    @field_validator("routine_id")
    @classmethod
    def safe_id(cls, value):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", value):
            raise ValueError("routine_id: lowercase letters, digits, '-' (max 32)")
        return value

    @model_validator(mode="after")
    def step_rules(self):
        kinds = [s.type for s in self.steps]
        if len(set(kinds)) != len(kinds):
            raise ValueError("each step type at most once")
        if "recommend" in kinds and kinds[-1] != "recommend":
            raise ValueError("recommend must be the last step (it fills the remaining time)")
        return self


class RoutinesIn(BaseModel):
    routines: list[Routine] = Field(max_length=5)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [r.routine_id for r in self.routines]
        if len(set(ids)) != len(ids):
            raise ValueError("routine_id must be unique")
        return self


class PlanIn(Situation):
    model_config = ConfigDict(allow_inf_nan=False)
    minutes: float | None = Field(None, gt=0, le=600)
    interests: list[str] = Field(default_factory=list, max_length=21)
    speed: float = Field(1.0, gt=0, le=3)
    hour: int | None = Field(None, ge=0, le=23)
    family: bool = False
    passengers: int | None = Field(None, ge=0, le=9)


def user_routines(listening, user_id):
    stored, updated_at = listening.routines(user_id)
    return (stored if stored is not None else DEFAULT_ROUTINES), updated_at, stored is None


@router.get("/me/routines")
def get_routines(user_id: CurrentUserDep, listening: ListeningDep):
    routines, updated_at, is_default = user_routines(listening, user_id)
    return {"routines": routines, "updated_at": updated_at, "default": is_default}


@router.put("/me/routines")
def put_routines(body: RoutinesIn, user_id: CurrentUserDep, listening: ListeningDep):
    routines = [r.model_dump(exclude_none=True) for r in body.routines]
    return {"routines": routines, "updated_at": listening.set_routines(user_id, routines), "default": False}


@router.post("/routines/{routine_id}/plan")
def plan_routine(routine_id: str, body: PlanIn, request: Request, user_id: CurrentUserDep, catalog: CatalogDep,
                 listening: ListeningDep, service: RecsysDep,
                 policy_provider: Annotated[Callable[[], PlaylistPolicy], Depends(get_policy_provider)]):
    routines, _, _ = user_routines(listening, user_id)
    routine = next((r for r in routines if r["routine_id"] == routine_id), None)
    if routine is None:
        raise HTTPException(404, "unknown routine")
    if body.minutes is None and body.remain_sec is None:
        raise HTTPException(422, "minutes or remain_sec is required")
    budget = body.minutes * 60 if body.minutes is not None else body.remain_sec
    briefing = get_briefing(request)
    items, skipped, clock, recommend = [], [], 0.0, None
    for step in routine["steps"]:
        kind = step["type"]
        if kind == "recommend":
            left = budget - clock
            if left < MIN_RECOMMEND_SEC:
                skipped.append({"type": kind, "reason": "no_time_left"})
                continue
            context = {k: v for k, v in {"remain_sec": body.remain_sec, "passengers": body.passengers,
                                         "hour": body.hour, "family": body.family or None}.items() if v is not None}
            recommend = run_recsys(user_id, left / 60, catalog=catalog, listening=listening, service=service,
                                   policy_provider=policy_provider, interests=body.interests, speed=body.speed,
                                   context=context, family=body.family, hour=body.hour)
            for item in recommend["items"]:
                items.append({**item, "routine_step": "recommend", "start_at_sec": round(clock + item["start_at_sec"], 3)})
            clock += recommend["total_sec"]
            continue
        b = briefing.build(kind, lat=body.lat, lon=body.lon, remain_sec=body.remain_sec, remain_m=body.remain_m)
        if not b["available"]:
            skipped.append({"type": kind, "reason": "unavailable", "message": b["text"]})
            continue
        if clock + b["duration_sec"] > budget:
            skipped.append({"type": kind, "reason": "no_time_left"})
            continue
        items.append({**b, "routine_step": kind, "start_at_sec": round(clock, 3)})
        clock += b["duration_sec"]
    return {"routine_id": routine_id, "name": routine["name"], "budget_sec": round(budget, 3),
            "total_sec": round(clock, 3), "leftover_sec": round(max(0.0, budget - clock), 3),
            "steps": [{"type": s["type"], "title": STEP_TITLES[s["type"]]} for s in routine["steps"]],
            "items": items, "skipped": skipped,
            "recommend": None if recommend is None else {
                k: recommend.get(k) for k in ("budget_sec", "total_sec", "shortage", "recsys", "signal_status")}}
