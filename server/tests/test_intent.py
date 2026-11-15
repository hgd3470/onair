import pytest

from conftest import API
from test_listening import event, register
from teum.intent.rules import parse


@pytest.mark.parametrize("text,intent", [("다음", "skip"), ("정지선 이야기 틀어줘", "play_content"), ("멈춰", "pause"),
                                         ("좋아요", "like"), ("이어서 들려줘", "resume_content"), ("안녕", "unknown")])
def test_rules(text, intent):
    assert parse(text)["intent"] == intent


def test_play_by_title(client):
    r = client.post(f"{API}/intent", json={"text": "블랙홀 이야기 틀어줘", "user_id": "u1"}).json()
    assert r["action"]["content_id"] == "story-a01" and r["action"]["audio_uri"].startswith(API)


def test_resume_latest_unfinished(client):
    user, auth = register(client)
    assert client.post(f"{API}/events", headers=auth, json=event(type="position", content_id="story-a02", position_sec=40)).status_code == 200
    r = client.post(f"{API}/intent", json={"text": "이어서 들려줘", "user_id": user}).json()
    assert r["action"] == {"type": "play", "content_id": "story-a02", "title": "조선의 과거 시험", "start_at_sec": 40,
                           "audio_uri": f"{API}/audio/story-a02.mp3"}


def test_player_page(client):
    assert "온에어" in client.get("/").text
