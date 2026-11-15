"""카탈로그 API: 목록, 1편 정보, 음원 파일."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from teum.core.deps import CatalogDep

router = APIRouter(tags=["catalog"])


@router.get("/contents")
def list_contents(catalog: CatalogDep, request: Request, category: str | None = None,
                  max_duration: float | None = Query(None, gt=0)):
    rows = catalog.stories
    if category:
        rows = [c for c in rows if c["category"] == category]
    if max_duration is not None:
        rows = [c for c in rows if c["duration_sec"] <= max_duration]
    return {"channel": request.app.state.settings.channel, "count": len(rows), "items": [catalog.public_view(c) for c in rows]}


@router.get("/contents/{content_id}")
def get_content(content_id: str, catalog: CatalogDep):
    c = catalog.get(content_id)
    if not c:  # 미승인·보류 콘텐츠도 '없음'과 똑같이 응답해서 존재 여부를 흘리지 않는다
        raise HTTPException(404, "not found")
    return {**catalog.public_view(c), "script": c["script"]}


def _serve(catalog, request):
    path = catalog.audio_path(request.url.path)
    if path is None:
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="audio/mpeg")


@router.get("/audio/{name}", include_in_schema=False)
def audio_file(name: str, catalog: CatalogDep, request: Request):
    return _serve(catalog, request)


@router.get("/ads/{name}", include_in_schema=False)
def ad_file(name: str, catalog: CatalogDep, request: Request):
    return _serve(catalog, request)


@router.get("/gen/{name}", include_in_schema=False)
def gen_file(name: str, catalog: CatalogDep, request: Request):  # 출처가 확인된 소재로 새로 만든 음원
    return _serve(catalog, request)
