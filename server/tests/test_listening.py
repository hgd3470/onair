"""토큰·이벤트 저장·배치 재계산·관심사 API 검증."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from conftest import API


def register(client):
    response = client.post(f"{API}/devices", json={"install_id": "same-install", "app_version": "0.1", "platform": "pleos"})
    assert response.status_code == 201
    device = response.json()
    return device["user_id"], {"Authorization": f"Bearer {device['token']}"}


def event(seq=1, type="position", position_sec=20, content_id="story-a01", **kwargs):
    return {"event_id": str(uuid4()), "content_id": content_id, "playback_id": "playback-1", "seq": seq,
            "type": type, "position_sec": position_sec,
            "client_ts": datetime.fromtimestamp(1700000000 + seq * 10, timezone.utc).isoformat(), **kwargs}


def test_devices(client):
    first, auth = register(client)
    second, other = register(client)
    assert first != second and auth != other
    assert first.startswith("u_") and auth["Authorization"].startswith("Bearer t_")


@pytest.mark.parametrize("header", [None, "Bearer unknown", "Basic unknown", "Bearer", "Bearer a b"])
def test_auth(client, header):
    headers = {} if header is None else {"Authorization": header}
    assert client.get(f"{API}/history", headers=headers).status_code == 401
    assert client.post(f"{API}/events", headers=headers, json=event()).status_code == 401


def test_roundtrip(client):
    user, auth = register(client)
    for e in [event(1, "play", 0), event(2, "position", 10, user_id="ignored"), event(3, "like", 999)]:  # 본문 user_id는 무시
        r = client.post(f"{API}/events", headers=auth, json=e)
        assert r.status_code == 200 and r.json() == {"accepted": 1, "duplicate": 0}
    history = client.get(f"{API}/history", headers=auth).json()
    assert history["user_id"] == user
    state = history["items"][0]
    assert state["played_sec"] == 10 and state["last_position_sec"] == 10 and state["liked"]
    assert state["last_ts"] == 1700000020.0
    _, other = register(client)
    assert client.get(f"{API}/history", headers=other, params={"user_id": user}).json()["items"] == []


def test_duplicate(client):
    _, auth = register(client)
    e = event()
    assert client.post(f"{API}/events", headers=auth, json=e).json()["accepted"] == 1
    _, other = register(client)
    assert client.post(f"{API}/events", headers=other, json=e).json() == {"accepted": 0, "duplicate": 1}
    with client.app.state.listening._connect() as db:
        assert db.execute("SELECT count(*) FROM event").fetchone()[0] == 1


@pytest.mark.parametrize("change", [{"type": "rewind"}, {"client_ts": "2026-11-03T12:00:00"},
                                    {"event_id": "not-a-uuid"}, {"client_ts": 123}])
def test_invalid_event(client, change):
    _, auth = register(client)
    assert client.post(f"{API}/events", headers=auth, json=event(**change)).status_code == 422


def test_event_value_validation(client):
    _, auth = register(client)
    missing_playback = event()
    missing_playback.pop("playback_id")
    invalid = [event(position_sec=-1), event(playback_rate=0), missing_playback]
    for e in invalid:
        assert client.post(f"{API}/events", headers=auth, json=e).status_code == 422

    r = client.post(f"{API}/events/batch", headers=auth, json={"events": [event(), *invalid]})
    assert r.status_code == 200
    assert r.json() == {"accepted": 1, "duplicate": 0, "rejected": [
        {"index": index, "event_id": e["event_id"], "reason": "invalid"}
        for index, e in enumerate(invalid, start=1)]}

    like = event(type="like")
    like.pop("playback_id")
    like.pop("position_sec")
    assert client.post(f"{API}/events", headers=auth, json=like).status_code == 200


def test_unknown_content(client):
    _, auth = register(client)
    assert client.post(f"{API}/events", headers=auth, json=event(content_id="story-x01")).status_code == 404


def test_batch_partial_and_retry(client):
    _, auth = register(client)
    missing = event(content_id="missing")
    events = [event(), {}, missing, None]
    r = client.post(f"{API}/events/batch", headers=auth, json={"events": events})
    assert r.status_code == 200
    assert r.json() == {"accepted": 1, "duplicate": 0, "rejected": [
        {"index": 1, "event_id": None, "reason": "invalid"},
        {"index": 2, "event_id": missing["event_id"], "reason": "unknown_content"},
        {"index": 3, "event_id": None, "reason": "invalid"}]}
    r = client.post(f"{API}/events/batch", headers=auth, json={"events": events}).json()
    assert r["accepted"] == 0 and r["duplicate"] == 1


@pytest.mark.parametrize("envelope", [{}, {"events": None}, {"events": {}}, []])
def test_batch_envelope(client, envelope):
    _, auth = register(client)
    assert client.post(f"{API}/events/batch", headers=auth, json=envelope).status_code == 422


def test_batch_limit(client):
    _, auth = register(client)
    assert client.post(f"{API}/events/batch", headers=auth, json={"events": [{}] * 501}).status_code == 413
    assert client.post(f"{API}/events/batch", headers=auth, json={"events": []}).json() == {
        "accepted": 0, "duplicate": 0, "rejected": []}


def test_batch_order_independent(client):
    _, first = register(client)
    _, second = register(client)
    events = [event(1, "play", 0), event(2, "position", 10), event(3, "like", None),
              event(4, "unlike", None), event(5, "position", 40)]
    assert client.post(f"{API}/events/batch", headers=first, json={"events": events}).json()["accepted"] == 5
    shuffled = [{**e, "event_id": str(uuid4())} for e in reversed(events)]
    assert client.post(f"{API}/events/batch", headers=second, json={"events": shuffled}).json()["accepted"] == 5
    assert client.get(f"{API}/history", headers=first).json()["items"] == client.get(f"{API}/history", headers=second).json()["items"]


@pytest.mark.parametrize("type", ["ad_start", "ad_complete", "ad_error"])
def test_ads(client, type):
    _, auth = register(client)
    e = event(type=type, content_id="ad-01")
    assert client.post(f"{API}/events", headers=auth, json=e).json()["accepted"] == 1
    assert client.get(f"{API}/history", headers=auth).json()["items"] == []
    e.update(type="position", content_id="story-a01")
    assert client.post(f"{API}/events", headers=auth, json=e).json()["duplicate"] == 1
    with client.app.state.listening._connect() as db:
        assert db.execute("SELECT count(*) FROM ad_event").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM event").fetchone()[0] == 0


def test_interests(client):
    user, auth = register(client)
    url = f"{API}/me/interests"
    assert client.get(url, headers=auth).json() == {"interests": [], "updated_at": None}
    saved = client.put(url, headers=auth, json={"interests": ["science", "history"]})
    assert saved.status_code == 200 and isinstance(saved.json()["updated_at"], float)
    assert client.get(url, headers=auth).json() == saved.json()
    assert client.app.state.listening.interests(user) == ["science", "history"]
    assert client.put(url, headers=auth, json={"interests": ["unknown"]}).status_code == 422
    assert client.get(url, headers=auth).json() == saved.json()
    assert client.put(url, headers=auth, json={"interests": []}).json()["interests"] == []
    _, other = register(client)
    assert client.get(url, headers=other).json()["updated_at"] is None


def test_late_events_recompute_and_versions(client):
    _, auth = register(client)
    def send(events):
        response = client.post(f"{API}/events/batch", headers=auth, json={"events": events})
        assert response.json()["accepted"] == len(events)
        return client.get(f"{API}/history", headers=auth).json()["items"][0]

    state = send([event(1, "play", 0), event(7, "complete", 60)])
    assert state["completed"] and state["played_sec"] == 60
    state = send([event(2, "pause", 10), event(6, "resume", 50)])
    assert not state["completed"] and state["played_sec"] == 20
    send([event(8, "like", None)])
    client.app.state.catalog.get("story-a01")["duration_sec"] = 100
    state = send([event(9, "play", 0, content_version=2), event(10, "position", 10, content_version=2)])
    assert state["played_sec"] == 10 and state["liked"] and not state["finished"]
    with client.app.state.listening._connect() as db:
        row = db.execute("SELECT * FROM listen_state").fetchone()
        assert row["base_version"] == 2 and row["duration_at_version"] == 100
    client.app.state.catalog.by_id.pop("story-a01")
    assert client.get(f"{API}/history", headers=auth).json()["items"] == []


def test_record_rolls_back_on_fold_failure(client, monkeypatch):
    from teum.listening import store
    from teum.listening.schemas import EventIn

    user, _ = register(client)
    def fail(*args):
        raise RuntimeError("fold failed")
    monkeypatch.setattr(store, "fold_content", fail)
    with pytest.raises(RuntimeError, match="fold failed"):
        client.app.state.listening.record(user, [EventIn.model_validate(event())], client.app.state.catalog)
    with client.app.state.listening._connect() as db:
        assert db.execute("SELECT count(*) FROM event").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM listen_state").fetchone()[0] == 0
