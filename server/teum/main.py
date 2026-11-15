"""온에어 API 서버. 담당 모듈의 라우터를 모아 /api/v1 아래에 붙인다.

실행: uvicorn teum.main:app --port 8765   (server/ 폴더에서)
참고: FastAPI 문서 Bigger Applications(APIRouter), Lifespan Events,
      fastapi/full-stack-fastapi-template backend/app/main.py (API_V1_STR 접두어).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from teum.briefing.router import router as briefing_router
from teum.briefing.service import BriefingService
from teum.catalog.router import router as catalog_router
from teum.catalog.service import Catalog, load_published
from teum.core.config import Settings
from teum.intent.router import router as intent_router
from teum.listening.router import router as listening_router
from teum.listening.store import ListeningStore
from teum.longform.router import router as longform_router
from teum.player.router import router as player_router
from teum.playlist.router import router as playlist_router
from teum.recsys.router import router as recsys_router
from teum.routine.router import router as routine_router
from teum.search.index import SearchIndex
from teum.search.router import router as search_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        rows = load_published(settings.catalog_db, channel=settings.channel)
        app.state.catalog = catalog = Catalog(rows, settings.audio_dirs, api_prefix=settings.api_prefix)
        app.state.search = SearchIndex(catalog.items, use_dense=settings.use_dense,
                                       index_dir=settings.index_dir, dense_model=settings.dense_model)
        app.state.listening = ListeningStore(settings.events_db)
        app.state.briefing = BriefingService()
        yield

    app = FastAPI(title="온에어 API", lifespan=lifespan, openapi_url=f"{settings.api_prefix}/openapi.json",
                  docs_url=f"{settings.api_prefix}/docs")
    # search를 catalog보다 먼저 붙인다: /contents/search가 /contents/{content_id}에 먹히지 않게
    for r in (search_router, catalog_router, intent_router, playlist_router, recsys_router, listening_router,
              briefing_router, routine_router, longform_router):
        app.include_router(r, prefix=settings.api_prefix)
    app.include_router(player_router)
    return app


app = create_app()
