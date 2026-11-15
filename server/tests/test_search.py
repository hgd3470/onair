from conftest import API
from test_listening import event, register


def test_finds_by_title(client):
    r = client.get(f"{API}/contents/search", params={"q": "블랙홀", "mode": "bm25"}).json()
    assert r["results"][0]["content_id"] == "story-a01"


def test_hidden_content_not_searchable(client):
    r = client.get(f"{API}/contents/search", params={"q": "공개하면 안 되는 대본", "mode": "bm25"}).json()
    assert all(x["content_id"] != "story-x01" for x in r["results"])


def test_history_scope_needs_user(client):
    assert client.get(f"{API}/contents/search", params={"q": "조선", "scope": "listening_history"}).status_code == 422
    user, auth = register(client)
    assert client.post(f"{API}/events", headers=auth, json=event(type="play", content_id="story-a02", position_sec=0)).status_code == 200
    r = client.get(f"{API}/contents/search", params={"q": "조선", "scope": "listening_history", "user_id": user}).json()
    assert [x["content_id"] for x in r["results"]] == ["story-a02"]
