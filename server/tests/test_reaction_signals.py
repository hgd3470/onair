"""S2-10: 넘긴 시점(user_next)과 골라 듣기 선택 이력이 저장되고 추천에 반영되는지 본다."""
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from conftest import API
from test_listening import register

NOW = datetime.now(timezone.utc).timestamp()


def ts(offset):
    return datetime.fromtimestamp(NOW - 3600 + offset, timezone.utc).isoformat()


def ev(seq, type, pos, cid, playback="pb-1", offset=None, **kw):
    return {"event_id": str(uuid4()), "content_id": cid, "playback_id": playback, "seq": seq, "type": type,
            "position_sec": pos, "client_ts": ts(pos if offset is None else offset), **kw}


def ids(response):
    assert response.status_code == 200, response.text
    return [c["content_id"] for c in response.json()["items"] if c["kind"] == "story"]


def test_user_next_skip_lowers_story(client):
    user, auth = register(client)
    params = dict(user_id=user, minutes=10)
    assert ids(client.get(f"{API}/playlist", params=params))[0] == "story-a01"
    # 3초 듣고 다음 버튼 → 이른 넘김
    for e in (ev(1, "play", 0, "story-a01"), ev(2, "skip", 3, "story-a01", skip_reason="user_next")):
        assert client.post(f"{API}/events", headers=auth, json=e).status_code == 200
    after = ids(client.get(f"{API}/playlist", params=params))
    assert after[0] != "story-a01" and "story-a01" in after
    from teum.playlist.skip_preferences import skip_penalties
    store, catalog = client.app.state.listening, client.app.state.catalog
    penalty = skip_penalties(catalog.items, store.history(user, catalog), store.playback_events(user),
                             now=datetime.now(timezone.utc))
    assert penalty["story-a01"]["kind"] == "early_skip" and penalty["story-a01"]["penalty"] > 0.4


def test_non_user_skip_is_neutral(client):
    """도착으로 끊긴 재생은 '그냥 멈춘 재생'과 같게 본다 (넘김 감점 없음)."""
    orders = {}
    for name, last in (("arrival", ev(2, "skip", 3, "story-a01", skip_reason="arrival")),
                       ("pause", ev(2, "pause", 3, "story-a01"))):
        user, auth = register(client)
        for e in (ev(1, "play", 0, "story-a01"), last):
            client.post(f"{API}/events", headers=auth, json=e)
        orders[name] = ids(client.get(f"{API}/playlist", params=dict(user_id=user, minutes=10)))
    assert orders["arrival"] == orders["pause"]
    user, auth = register(client)
    for e in (ev(1, "play", 0, "story-a01"), ev(2, "skip", 3, "story-a01", skip_reason="user_next")):
        client.post(f"{API}/events", headers=auth, json=e)
    skipped = ids(client.get(f"{API}/playlist", params=dict(user_id=user, minutes=10)))
    assert skipped.index("story-a01") >= orders["pause"].index("story-a01")


def test_skip_reason_only_kept_on_skip_and_old_db_is_migrated(client, tmp_path):
    user, auth = register(client)
    client.post(f"{API}/events", headers=auth, json=ev(1, "play", 0, "story-a01", skip_reason="user_next"))
    db = sqlite3.connect(tmp_path / "events.sqlite")
    assert db.execute("SELECT skip_reason FROM event").fetchone()[0] is None
    # 예전 스키마 DB에 열이 없더라도 다시 열면 붙는다
    old = tmp_path / "old.sqlite"
    con = sqlite3.connect(old)
    con.execute("CREATE TABLE event (id INTEGER PRIMARY KEY, event_id TEXT UNIQUE, user_id TEXT, content_id TEXT,"
                " content_version INTEGER, playback_id TEXT, seq INTEGER, type TEXT, position_sec REAL,"
                " playback_rate REAL, client_ts REAL, received_at REAL)")
    con.commit()
    con.close()
    from teum.listening.store import ListeningStore
    ListeningStore(old)
    cols = {r[1] for r in sqlite3.connect(old).execute("PRAGMA table_info(event)")}
    assert {"skip_reason", "selection_id"} <= cols


def test_ad_events_still_accepted(client):
    _, auth = register(client)
    body = {"event_id": str(uuid4()), "content_id": "ad-01", "seq": 1, "type": "ad_start", "client_ts": ts(0)}
    assert client.post(f"{API}/events", headers=auth, json=body).status_code == 200


def test_selection_counts_only_after_play(client):
    user, auth = register(client)
    sid = str(uuid4())
    sel = {"selection_id": sid, "interests": ["C13"], "selected_at": ts(0)}
    assert client.post(f"{API}/selections", headers=auth, json=sel).json() == {"accepted": 1, "duplicate": 0}
    assert client.post(f"{API}/selections", headers=auth, json=sel).json() == {"accepted": 0, "duplicate": 1}
    store = client.app.state.listening
    assert [s.playback_started for s in store.selections(user)] == [False]
    client.post(f"{API}/events", headers=auth, json=ev(1, "play", 0, "story-a01", selection_id=sid))
    rows = store.selections(user)
    assert rows[0].playback_started and rows[0].group_ids == ("C",)


def test_play_before_selection_still_marks_started(client):
    user, auth = register(client)
    sid = str(uuid4())
    client.post(f"{API}/events", headers=auth, json=ev(1, "play", 0, "story-a01", selection_id=sid))
    client.post(f"{API}/selections", headers=auth, json={"selection_id": sid, "interests": ["C08"], "selected_at": ts(0)})
    assert client.app.state.listening.selections(user)[0].playback_started


def test_selection_validation(client):
    _, auth = register(client)
    bad = {"selection_id": str(uuid4()), "interests": ["science"], "selected_at": ts(0)}
    assert client.post(f"{API}/selections", headers=auth, json=bad).status_code == 422
    assert client.post(f"{API}/selections", json={**bad, "interests": ["C01"]}).status_code == 401


def test_selection_history_reaches_ranker(client):
    """고르고 들은 대분류는 다음 추천의 점수와 이유 코드에 남는다."""
    user, auth = register(client)
    sid = str(uuid4())
    client.post(f"{API}/selections", headers=auth,
                json={"selection_id": sid, "interests": ["C01"], "selected_at": ts(0)})
    client.post(f"{API}/events", headers=auth, json=ev(1, "play", 0, "story-a01", selection_id=sid))
    selections = client.app.state.listening.selections(user)
    from teum.playlist.selection_preferences import group_preferences
    prefs = group_preferences(selections, now=datetime.now(timezone.utc))
    assert prefs.get("A", 0) > 0
