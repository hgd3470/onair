"""검색 API. scope=listening_history면 그 사용자가 들은 것 안에서만 찾는다."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from teum.core.deps import CatalogDep, ListeningDep, SearchDep

router = APIRouter(tags=["search"])


@router.get("/contents/search")
def search(index: SearchDep, catalog: CatalogDep, listening: ListeningDep, q: str = Query(..., min_length=1),
           k: int = Query(5, ge=1, le=20), mode: Literal["bm25", "dense", "hybrid"] = "hybrid",
           scope: Literal["catalog", "listening_history"] = "catalog", user_id: str | None = None):
    if scope == "listening_history" and not user_id:
        raise HTTPException(422, "scope=listening_history에는 user_id가 필요함")
    restrict = set(listening.history(user_id, catalog)) if scope == "listening_history" else None
    return {"query": q, "mode": mode, "scope": scope, "results": index.search(q, k=k, mode=mode, restrict_ids=restrict)}
