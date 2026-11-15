"""라우터가 받아 쓰는 공용 객체. 서버가 뜰 때 lifespan이 app.state에 올려 둔다.

참고: FastAPI 문서 Lifespan Events, fastapi/full-stack-fastapi-template backend/app/api/deps.py (Annotated 의존성).
테스트는 app.dependency_overrides 대신 create_app(settings)에 임시 경로를 넣어 바꾼다.
"""
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from teum.catalog.service import Catalog
from teum.listening.store import ListeningStore
from teum.search.index import SearchIndex


def get_catalog(request: Request) -> Catalog:
    return request.app.state.catalog


def get_search(request: Request) -> SearchIndex:
    return request.app.state.search


def get_listening(request: Request) -> ListeningStore:
    return request.app.state.listening


CatalogDep = Annotated[Catalog, Depends(get_catalog)]
SearchDep = Annotated[SearchIndex, Depends(get_search)]
ListeningDep = Annotated[ListeningStore, Depends(get_listening)]


def get_current_user(request: Request, listening: ListeningDep) -> str:
    parts = request.headers.get("Authorization", "").split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})
    user_id = listening.user_for_token(parts[1])
    if user_id is None:
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})
    return user_id


CurrentUserDep = Annotated[str, Depends(get_current_user)]
