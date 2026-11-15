from conftest import API
from test_listening import event, register


def test_fits_budget_and_inserts_ad(client):
    r = client.get(f"{API}/playlist", params={"user_id": "u1", "minutes": 4}).json()
    assert r["total_sec"] <= r["budget_sec"]
    kinds = [x["kind"] for x in r["items"]]
    assert kinds[0] == kinds[-1] == "story"
    assert "ad" in kinds
    assert ["ad", "ad"] not in [kinds[i:i + 2] for i in range(len(kinds) - 1)]
    assert .10 <= r["advertising"]["actual_ratio"] <= .15


def test_interest_first_and_completed_removed(client):
    r = client.get(f"{API}/playlist", params={"user_id": "u1", "minutes": 10, "interests": "funny"}).json()
    assert r["items"][0]["content_id"] == "story-a03"
    user, auth = register(client)
    assert client.post(f"{API}/events", headers=auth, json=event(1, "play", 0, "story-a03")).status_code == 200
    assert client.post(f"{API}/events", headers=auth, json=event(2, "complete", 45, "story-a03", client_ts="2023-11-14T22:14:15+00:00")).status_code == 200
    r = client.get(f"{API}/playlist", params={"user_id": user, "minutes": 10, "interests": "funny"}).json()
    assert "story-a03" not in [x["content_id"] for x in r["items"]]
