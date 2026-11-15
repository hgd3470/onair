"""기기 토큰으로 청취 기록과 관심사를 저장·조회한다."""
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from teum.core.deps import CatalogDep, CurrentUserDep, ListeningDep
from teum.listening.schemas import BatchIn, DeviceIn, EventIn, InterestsIn, SelectionIn
from teum.playlist.topics import CATEGORY_CODES, GROUPS

router = APIRouter(tags=["listening"])


@router.post("/devices", status_code=201)
def register_device(device: DeviceIn, listening: ListeningDep):
    user_id, token = listening.register_device(device.install_id, device.app_version, device.platform)
    return {"user_id": user_id, "token": token}


@router.post("/events")
def post_event(e: EventIn, user_id: CurrentUserDep, catalog: CatalogDep, listening: ListeningDep):
    result = listening.record(user_id, [e], catalog)
    if result["rejected"]:
        raise HTTPException(404, "not found")
    return {"accepted": result["accepted"], "duplicate": result["duplicate"]}


@router.get("/history")
def history(user_id: CurrentUserDep, catalog: CatalogDep, listening: ListeningDep):
    return {"user_id": user_id, "items": list(listening.history(user_id, catalog).values())}


@router.post("/events/batch")
def post_batch(batch: BatchIn, user_id: CurrentUserDep, catalog: CatalogDep, listening: ListeningDep):
    if len(batch.events) > 500:
        raise HTTPException(413, "at most 500 events")
    valid, indices, rejected = [], [], []
    for index, item in enumerate(batch.events):
        try:
            valid.append(EventIn.model_validate(item))
            indices.append(index)
        except ValidationError:
            event_id = item.get("event_id") if isinstance(item, dict) else None
            rejected.append({"index": index, "event_id": event_id if isinstance(event_id, str) else None, "reason": "invalid"})
    result = listening.record(user_id, valid, catalog)
    for item in result["rejected"]:
        item["index"] = indices[item["index"]]
    result["rejected"] = sorted(rejected + result["rejected"], key=lambda item: item["index"])
    return result


@router.post("/selections")
def post_selection(body: SelectionIn, user_id: CurrentUserDep, listening: ListeningDep):
    """골라 듣기에서 고른 주제. 이어지는 play 이벤트에 같은 selection_id를 넣으면 '고르고 들었다'로 반영된다."""
    unknown = set(body.interests) - CATEGORY_CODES
    if unknown:
        raise HTTPException(422, "unknown interest category; send expanded C codes")
    groups = {g for g, codes in GROUPS.items() if codes.intersection(body.interests)}
    return listening.record_selection(user_id, body, groups)


@router.put("/me/interests")
def put_interests(body: InterestsIn, user_id: CurrentUserDep, catalog: CatalogDep, listening: ListeningDep):
    categories = {c["category"] for c in catalog.items}
    if any(interest not in categories for interest in body.interests):
        raise HTTPException(422, "unknown category")
    return listening.set_interests(user_id, body.interests)


@router.get("/me/interests")
def get_interests(user_id: CurrentUserDep, listening: ListeningDep):
    return listening.interests_with_time(user_id)
