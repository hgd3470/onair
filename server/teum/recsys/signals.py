"""청취 기록(listen_state)에서 추천 전략이 쓰는 집계를 읽는다. 읽기만 한다.

성공 = 완청 또는 좋아요. 실패 = 넘김(skipped)인데 완청·좋아요가 없는 경우.
듣다 만 것(재생했지만 완청도 넘김도 아님)은 중립으로 둔다.
"""
import sqlite3
from collections import defaultdict
from pathlib import Path


def _rows(db_path):
    path = Path(db_path)
    if not path.exists():
        return []
    try:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        has = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='listen_state'").fetchone()
        if not has:
            return []
        return db.execute("SELECT user_id, content_id, completed, skipped, liked, saved, played_sec "
                          "FROM listen_state").fetchall()
    except sqlite3.Error:
        return []  # 청취 DB를 못 읽으면 다른 사용자 신호 없이(중립) 추천한다
    finally:
        db.close()


def outcome(completed, skipped, liked):
    if completed or liked:
        return 1
    if skipped:
        return 0
    return None


def content_stats(db_path):
    """content_id → [성공 수, 실패 수] (사용자 단위)."""
    stats = defaultdict(lambda: [0, 0])
    for _user, cid, completed, skipped, liked, _saved, _played in _rows(db_path):
        result = outcome(completed, skipped, liked)
        if result is not None:
            stats[cid][1 - result] += 1
    return dict(stats)


def positives(db_path):
    """user_id → 좋게 반응한 content_id 집합 (완청·좋아요·보관). EASE 학습 입력."""
    users = defaultdict(set)
    for user, cid, completed, _skipped, liked, saved, _played in _rows(db_path):
        if completed or liked or saved:
            users[user].add(cid)
    return dict(users)


def counts(db_path):
    rows = _rows(db_path)
    return {"users": len({r[0] for r in rows}), "listen_rows": len(rows),
            "positives": sum(1 for r in rows if r[2] or r[4] or r[5])}
