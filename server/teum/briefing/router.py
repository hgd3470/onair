"""퀵 메뉴·브리핑 HTTP (S2-12).

위치는 개인 정보라 URL에 싣지 않도록 POST 본문으로 받는다(접속 기록에 남지 않게). 저장하지 않는다.
앱은 위도·경도를 소수 둘째 자리(약 1km)로 반올림해서 보내면 된다.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from teum.briefing.service import KINDS, TITLES, BriefingService

router = APIRouter(tags=["briefing"])


class Situation(BaseModel):
    """앱이 아는 차량·위치 상황. 모두 선택. remain_*는 NaviHelper onDestinationInfo 값."""
    model_config = ConfigDict(allow_inf_nan=False)
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    remain_sec: float | None = Field(None, gt=0, le=36000)
    remain_m: float | None = Field(None, ge=0, le=2_000_000)


def get_briefing(request: Request) -> BriefingService:
    service = getattr(request.app.state, "briefing", None)
    if service is None:
        service = request.app.state.briefing = BriefingService()
    return service


def build(request: Request, kind: str, body: Situation):
    return get_briefing(request).build(kind, lat=body.lat, lon=body.lon,
                                       remain_sec=body.remain_sec, remain_m=body.remain_m)


@router.post("/quick-menu")
def quick_menu(request: Request, body: Situation | None = None):
    """홈 화면 퀵 메뉴 카드 3개. 카드의 부제·예상 길이와 바로 읽을 낭독 문장을 함께 준다."""
    body = body or Situation()
    return {"items": [build(request, kind, body) for kind in KINDS]}


@router.post("/briefings/{kind}")
def briefing(kind: str, request: Request, body: Situation | None = None):
    if kind not in KINDS:
        raise HTTPException(404, f"unknown briefing; one of {', '.join(KINDS)}")
    return build(request, kind, body or Situation())


@router.get("/briefings")
def briefing_kinds():
    return {"items": [{"id": k, "title": TITLES[k]} for k in KINDS]}
