"""설계안 listen_state 절의 예시 표를 그대로 테스트로 옮긴 것."""
import random
from copy import deepcopy

import pytest

from teum.listening.state import fold_content


def events(notation, *, rate=None, playback_id="p1", first_seq=1):
    result = []
    for seq, token in enumerate(notation.split(), first_seq):
        typ_pos, timestamp = token.split("/")
        typ, _, position = typ_pos.partition("@")
        result.append({
            "event_id": f"e{seq}", "content_version": None,
            "playback_id": playback_id, "seq": seq, "type": typ,
            "position_sec": float(position) if position else None,
            "playback_rate": rate, "client_ts": 1000.0 + float(timestamp),
        })
    return result


def positions(start, stop, step=10, delay=0):
    return " ".join(f"position@{p}/{p + delay}" for p in range(start, stop, step))


FULL = events(f"play@0/0 {positions(10, 100)} complete@100/100")
FAST = "play@0/0 " + " ".join(f"position@{15*t}/{10*t}" for t in range(1, 7)) + " complete@100/66.7"
REACTIONS = events("position@50/50 like/50 unlike/55 position@60/60", first_seq=40)
REACTIONS = [REACTIONS[i] for i in (0, 2, 1, 3)]
ROWS = [
    ("끝까지 들음", FULL, 100, True, True, 100),
    ("탐색 경계 없음", events("play@0/0 position@10/10 position@93/20 complete@100/27"), 17, False, True, 100),
    ("탐색 경계 있음", events("play@0/0 position@10/10 position@12/12 position@85/12 position@93/20 complete@100/27"), 27, False, True, 100),
    ("1.5배속 보고", events(FAST, rate=1.5), 100, True, True, 100),
    ("배속 누락", events(FAST), 0, False, True, 100),
    ("Android 안내 되감기", events("play@0/0 position@10/10 duck_start@15/15 duck_end@12.5/35 position@22.5/45 pause@30/52.5"), 30, False, False, 30),
    ("웹 안내 4초", events("play@0/0 position@10/10 duck_start@15/15 duck_end@19/19 position@30/30 pause@35/35"), 31, False, False, 35),
    ("내비 안내 중 사용자 정지", events("play@0/0 position@10/10 duck_start@15/15 pause@15/18 duck_end@15/35 resume@15/60 position@25/70 pause@30/75"), 30, False, False, 30),
    ("좋아요 취소 역순 도착", REACTIONS, 10, False, False, 60),
    ("큐 넘침", events(f"play@0/0 {positions(10, 60)} position@90/90 complete@100/100"), 100, True, True, 100),
    ("complete만 도착", events("complete@100/100"), 0, False, True, 100),
    ("play complete만 남음", events("play@0/0 complete@100/100"), 100, True, True, 100),
    ("정지 재개 늦게 도착", events(f"play@0/0 pause@40/40 resume@40/100 {positions(50, 100, delay=60)} complete@100/160"), 100, True, True, 100),
    ("정지 중 탐색", events("play@0/0 pause@10/10 resume@90/90 complete@100/100"), 20, False, True, 100),
    ("웹 안내 25초 경계 지연", events(f"play@0/0 {positions(10, 50)} duck_start@45/45 duck_end@70/70 position@80/80 position@90/90 complete@100/100"), 75, False, True, 100),
    ("완청 뒤 재청취 정지", FULL + events("play@0/0 position@10/10 position@20/20 pause@30/30", playback_id="p2", first_seq=12), 100, True, False, 30),
]


@pytest.mark.parametrize("name,source,played,completed,finished,last_position", ROWS, ids=[row[0] for row in ROWS])
def test_design_examples(name, source, played, completed, finished, last_position):
    before = deepcopy(source)
    state = fold_content(iter(source), 100.0)
    assert state["played_sec"] == pytest.approx(played)
    assert state["completed"] is completed
    assert state["finished"] is finished
    assert state["last_position_sec"] == last_position
    assert state["liked"] is False
    assert state["last_ts"] == max(source, key=lambda e: e["seq"])["client_ts"]
    assert source == before


def test_order_independence():
    rng = random.Random(20261103)
    for _, source, *_ in ROWS:
        shuffled = list(source)
        rng.shuffle(shuffled)
        assert fold_content(shuffled, 100) == fold_content(sorted(source, key=lambda e: e["seq"]), 100)


def test_version_scope_and_reactions():
    source = events("play@0/0 complete@100/100 skip@100/101 like/102 save/103 unsave/104")
    current = events("play@0/105 pause@10/115", playback_id="p2", first_seq=7)
    for event in current:
        event["content_version"] = 3
    state = fold_content(source + current, 100, base_version=2)
    assert state == {
        "last_position_sec": 10.0, "played_sec": 10.0, "completed": False,
        "finished": False, "skipped": False, "liked": True, "saved": False,
        "last_ts": 1115.0, "content_version": 3, "duration_at_version": 100,
        "base_version": 2, "prev_completed": False,
    }


def test_previous_completion_without_listening():
    assert fold_content([], 100, base_version=2, prev_completed=True) == {
        "last_position_sec": 0.0, "played_sec": 0.0, "completed": True,
        "finished": False, "skipped": False, "liked": False, "saved": False,
        "last_ts": None, "content_version": 2, "duration_at_version": 100,
        "base_version": 2, "prev_completed": True,
    }
