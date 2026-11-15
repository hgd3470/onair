"""서버 설정. 모든 경로와 스위치는 환경변수(TEUM_*)로 바꾼다.

참고: fastapi/full-stack-fastapi-template backend/app/core/config.py (Settings 한 곳에 모으기).
pydantic-settings가 서버 venv에 없어서 같은 모양을 pydantic BaseModel + from_env로 만든다.
"""
import os
from pathlib import Path

from pydantic import BaseModel

REPO = Path(__file__).resolve().parents[3]


class Settings(BaseModel):
    data_root: Path                      # 콘텐츠 데이터가 있는 실행 폴더 (inputs/, outputs/)
    events_db: Path                      # 청취 기록 SQLite 파일
    channel: str = "internal_demo"       # public이면 공개 채널 콘텐츠만 올린다
    use_dense: bool = True               # False면 임베딩 검색 없이 BM25만
    dense_model: str = "dragonkue/BGE-m3-ko"  # Hugging Face 모델 이름이나 로컬 경로 (TEUM_DENSE_MODEL)
    api_prefix: str = "/api/v1"
    longform_out: Path | None = None      # 롱폼 회차 폴더 (ai/longform 결과, TEUM_HOME/longform/out)

    @property
    def catalog_db(self) -> Path:
        return self.data_root / "outputs" / "catalog.sqlite"

    @property
    def index_dir(self) -> Path:
        return self.data_root / "outputs" / "index"

    @property
    def audio_dirs(self) -> dict[str, Path]:
        """음원 URI 앞부분(/audio, /ads, /gen) → 실제 폴더."""
        demo = self.data_root / "inputs" / "demo_snapshot"
        return {"audio": demo / "audio", "ads": demo / "ads", "gen": self.data_root / "outputs" / "gen" / "audio"}

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_root=Path(os.environ.get("TEUM_DATA_ROOT", REPO / "content" / "demo_data")),  # 기본: 저장소의 데모 데이터
            events_db=Path(os.environ.get("TEUM_EVENTS_DB", REPO / "var" / "events.sqlite")),
            dense_model=os.environ.get("TEUM_DENSE_MODEL", "dragonkue/BGE-m3-ko"),
            channel=os.environ.get("TEUM_CHANNEL", "internal_demo"),
            use_dense=os.environ.get("TEUM_DENSE", "1") == "1",
            longform_out=_longform_out(),
        )


def _longform_out():
    if os.environ.get("TEUM_LONGFORM_OUT"):
        return Path(os.environ["TEUM_LONGFORM_OUT"])
    return Path(os.environ["TEUM_HOME"]) / "longform" / "out" if os.environ.get("TEUM_HOME") else None
