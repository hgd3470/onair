"""노출 기록: 어떤 사용자에게 어떤 전략으로 무엇을 몇 번째에 보여 줬는지 남긴다.

청취 기록(listen_state)은 "들었다"만 알고 "보여 줬는데 안 들었다"를 모른다. 전략을 비교하려면
이 기록이 있어야 한다(오프라인 평가, 밴딧 학습). 청취 DB와 섞지 않게 파일을 따로 둔다.
"""
import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS serve (
    request_id TEXT PRIMARY KEY, ts REAL, user_id TEXT, algorithm TEXT, requested TEXT,
    fallback TEXT, assignment TEXT, strategy_version TEXT, minutes REAL, context TEXT, items TEXT);
CREATE INDEX IF NOT EXISTS serve_user_ts ON serve(user_id, ts);
"""


class ServeLog:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self._ready = False

    def _open(self):
        if not self._ready:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=2)
        if not self._ready:
            db.executescript(SCHEMA)
            self._ready = True
        return db

    def write(self, user_id, algorithm, requested, fallback, assignment, version, minutes, context, items):
        request_id = str(uuid.uuid4())
        rows = [{"content_id": c["content_id"], "kind": c["kind"], "position": i,
                 "score": c.get("recommendation_score"), "bonus": (c.get("recsys") or {}).get("bonus")}
                for i, c in enumerate(items)]
        db = self._open()
        try:
            with db:
                db.execute("INSERT INTO serve VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (request_id, time.time(), user_id, algorithm, requested, fallback, assignment, version,
                            minutes, json.dumps(context, ensure_ascii=False), json.dumps(rows, ensure_ascii=False)))
        finally:
            db.close()
        return request_id

    def read(self):
        if not self.db_path.exists():
            return []
        db = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in db.execute("SELECT * FROM serve ORDER BY ts")]
        finally:
            db.close()

    def count(self):
        if not self.db_path.exists():
            return 0
        db = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            has = db.execute("SELECT 1 FROM sqlite_master WHERE name='serve'").fetchone()
            return db.execute("SELECT count(*) FROM serve").fetchone()[0] if has else 0
        finally:
            db.close()
