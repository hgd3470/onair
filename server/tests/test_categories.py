import sqlite3

import pytest
from fastapi.testclient import TestClient

from conftest import API, make_catalog
from teum.catalog.categories import expand, migrate
from teum.core.config import Settings
from teum.main import create_app
from teum.search.index import category_words


def test_expand():
    expected = ["C01", "C02", "C03", "C04", "C05", "C13", "C21"]
    assert expand(["A", "C13", "E"]) == expected
    assert expand(["A", "C13", "E", "C01", "A", "C21"]) == expected
    with pytest.raises(ValueError):
        expand(["P01"])


def test_migrate(tmp_path):
    path = tmp_path / "catalog.sqlite"
    rows = [("story-s01", "story", "funny"), ("default", "story", "science"),
            ("unknown", "story", "funny"), ("story-h01", "ad", "funny"),
            ("story-i01", "story", "ad"), ("story-e01", "story", "C21")]
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE content (content_id TEXT PRIMARY KEY, kind TEXT, category TEXT)")
        db.executemany("INSERT INTO content VALUES (?, ?, ?)", rows)
    expected = ([("default", "science", "C13"), ("story-s01", "funny", "C02")], [("unknown", "funny")])
    before = path.read_bytes()
    assert migrate(path, dry_run=True) == expected
    assert path.read_bytes() == before
    assert migrate(path) == expected
    with sqlite3.connect(path) as db:
        actual = dict(db.execute("SELECT content_id, category FROM content"))
    assert actual == {cid: {"default": "C13", "story-s01": "C02"}.get(cid, cat) for cid, kind, cat in rows}
    assert migrate(path) == ([], [("unknown", "funny")])


def test_code_category_search_and_intent(tmp_path):
    make_catalog(tmp_path)
    with sqlite3.connect(tmp_path / "outputs" / "catalog.sqlite") as db:
        db.execute("UPDATE content SET category='C13' WHERE content_id='story-a01'")
    settings = Settings(data_root=tmp_path, events_db=tmp_path / "events.sqlite", use_dense=False)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"{API}/contents/search", params={"q": "과학 이야기", "mode": "bm25"})
        assert response.status_code == 200
        assert response.json()["results"][0]["content_id"] == "story-a01"
        response = client.post(f"{API}/intent", json={"text": "과학 이야기 들려줘", "user_id": "u1"})
        assert response.status_code == 200
        assert response.json()["action"]["content_id"] == "story-a01"
    assert {"과학", "기술", "AI", "우주"} <= category_words({"category": "C13"})
    assert "과학" in category_words({"category": "science"})
