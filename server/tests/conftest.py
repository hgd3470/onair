"""테스트용 작은 카탈로그를 임시 폴더에 만들고, 그 경로를 넣은 앱을 띄운다.

참고: FastAPI 문서 Testing, fastapi/full-stack-fastapi-template backend/tests/conftest.py (client fixture).
실제 콘텐츠 데이터 없이 돈다. 임베딩 검색은 끄고 BM25로만 본다.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from teum.core.config import Settings  # noqa: E402
from teum.main import create_app  # noqa: E402

# (content_id, kind, title, category, tags, duration_sec, status, play_no, script)
CONTENTS = [
    ("story-a01", "story", "블랙홀은 어떻게 생길까", "science", ["블랙홀", "우주"], 60.0, "published", 1, "별이 무너지면 블랙홀이 된다"),
    ("story-a02", "story", "조선의 과거 시험", "history", ["조선"], 90.0, "published", 2, "조선 선비들은 과거 시험을 봤다"),
    ("story-a03", "story", "회사에서 생긴 웃긴 썰", "funny", ["회사"], 45.0, "published", 3, "부장님이 회의 중에 졸았다"),
    ("story-x01", "story", "보류된 이야기", "funny", [], 30.0, "on_hold", 4, "아직 공개하면 안 되는 대본"),
    ("ad-01", "ad", "광고 하나", "ad", [], 15.0, "published", None, "광고 대본"),
]


def make_catalog(root: Path):
    out = root / "outputs"
    audio = root / "inputs" / "demo_snapshot" / "audio"
    ads = root / "inputs" / "demo_snapshot" / "ads"
    for d in (out, audio, ads):
        d.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(out / "catalog.sqlite")
    db.executescript("""
        CREATE TABLE source (source_id TEXT PRIMARY KEY, license TEXT, attribution TEXT, url TEXT);
        CREATE TABLE script (script_id TEXT PRIMARY KEY, text TEXT);
        CREATE TABLE audio_asset (audio_id TEXT PRIMARY KEY, uri TEXT);
        CREATE TABLE content (content_id TEXT PRIMARY KEY, kind TEXT, title TEXT, category TEXT, tags TEXT, duration_sec REAL,
                              channel TEXT, play_no INTEGER, audio_id TEXT, script_id TEXT, source_id TEXT, status TEXT);
    """)
    for cid, kind, title, cat, tags, dur, status, no, text in CONTENTS:
        folder = "ads" if kind == "ad" else "audio"
        (root / "inputs" / "demo_snapshot" / folder / f"{cid}.mp3").write_bytes(b"ID3")
        db.execute("INSERT INTO script VALUES (?,?)", (cid, text))
        db.execute("INSERT INTO audio_asset VALUES (?,?)", (cid, f"/{folder}/{cid}.mp3"))
        db.execute("INSERT INTO content VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (cid, kind, title, cat, json.dumps(tags, ensure_ascii=False), dur, "internal_demo", no, cid, cid, None, status))
    db.commit()
    db.close()


@pytest.fixture()
def client(tmp_path):
    make_catalog(tmp_path)
    settings = Settings(data_root=tmp_path, events_db=tmp_path / "events.sqlite", use_dense=False)
    with TestClient(create_app(settings)) as c:  # with 안에서 lifespan이 돈다
        yield c


API = "/api/v1"
