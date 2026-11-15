"""음성 명령 API. 입력은 Pleos STT가 돌려준 문장이다."""
from fastapi import APIRouter
from pydantic import BaseModel

from teum.core.deps import CatalogDep, ListeningDep, SearchDep
from teum.intent.resolver import resolve

router = APIRouter(tags=["intent"])


class IntentIn(BaseModel):
    text: str
    user_id: str


@router.post("/intent")
def resolve_intent(body: IntentIn, catalog: CatalogDep, index: SearchDep, listening: ListeningDep):
    return resolve(body.text, body.user_id, catalog, index, listening)
