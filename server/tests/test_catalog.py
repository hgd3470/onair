from conftest import API


def test_lists_only_published_stories(client):
    ids = [c["content_id"] for c in client.get(f"{API}/contents").json()["items"]]
    assert ids == ["story-a01", "story-a02", "story-a03"]


def test_filters(client):
    assert client.get(f"{API}/contents", params={"category": "history"}).json()["count"] == 1
    assert client.get(f"{API}/contents", params={"max_duration": 50}).json()["count"] == 1
    assert client.get(f"{API}/contents", params={"max_duration": 0}).status_code == 422


def test_hidden_content_looks_missing(client):
    assert client.get(f"{API}/contents/story-x01").status_code == 404
    assert client.get(f"{API}/contents/story-a01").json()["script"] == "별이 무너지면 블랙홀이 된다"


def test_audio_only_for_published(client):
    item = client.get(f"{API}/contents/story-a01").json()
    assert item["audio_uri"] == f"{API}/audio/story-a01.mp3"
    assert client.get(item["audio_uri"]).status_code == 200
    assert client.get(f"{API}/audio/story-x01.mp3").status_code == 404   # 파일은 있지만 보류 콘텐츠
    assert client.get(f"{API}/audio/..%2F..%2Foutputs%2Fcatalog.sqlite").status_code == 404
