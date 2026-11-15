import json

from fastapi.testclient import TestClient

from conftest import API, make_catalog
from teum.core.config import Settings
from teum.main import create_app


def test_lists_only_voiced_episodes(tmp_path):
    make_catalog(tmp_path)
    out = tmp_path / "longform" / "out"
    for pid, voiced in (("0-sisa", True), ("4-love", False)):
        d = out / pid
        d.mkdir(parents=True)
        (d / "script.json").write_text(json.dumps({"show": ["기자", "친구", "훅"], "script": {
            "title": f"{pid} 제목", "lines": [{"speaker": "A", "text": "안녕"}, {"speaker": "B", "text": "궁금해"}]}}), encoding="utf-8")
        if voiced:
            (d / "episode.mp3").write_bytes(b"ID3")
            (d / "episode.json").write_text(json.dumps({"duration_sec": 554.9}), encoding="utf-8")
    s = Settings(data_root=tmp_path, events_db=tmp_path / "e.sqlite", use_dense=False, longform_out=out)
    with TestClient(create_app(s)) as c:
        d = c.get(f"{API}/longform").json()
        assert d["count"] == 1
        it = d["items"][0]
        assert (it["content_id"], it["title"], it["hosts"], it["hook"], it["duration_sec"]) == \
            ("lf-0-sisa", "0-sisa 제목", ["기자", "친구"], "궁금해", 554.9)
        assert c.get(it["audio_uri"]).status_code == 200
        assert c.get(f"{API}/longform/4-love.mp3").status_code == 404
        assert c.get(f"{API}/longform/..%2Fx.mp3").status_code == 404
