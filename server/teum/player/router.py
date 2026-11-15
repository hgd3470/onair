"""시연용 웹 플레이어 (Pleos 앱 자리). 같은 API를 폰 브라우저에서 시험한다."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["player"])


@router.get("/", include_in_schema=False)
def player():
    return FileResponse(Path(__file__).parent / "static" / "player.html")
