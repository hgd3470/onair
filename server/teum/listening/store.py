"""청취 이벤트 원본과 접은 상태를 같은 트랜잭션에 저장한다."""
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from teum.listening.state import fold_content
from teum.playlist.selection_preferences import TopicSelection

PLAYBACK_TYPES = ("play", "position", "pause", "resume", "skip", "complete", "duck_start", "duck_end")
EVENT_WINDOW_SEC = 30 * 86400  # 넘김·선택 신호는 30일 안의 기록만 쓴다


class ListeningStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS device (
                    id INTEGER PRIMARY KEY, install_id TEXT, user_id TEXT UNIQUE, token TEXT UNIQUE,
                    app_version TEXT, platform TEXT, created_at REAL, last_seen_at REAL);
                CREATE TABLE IF NOT EXISTS event (
                    id INTEGER PRIMARY KEY, event_id TEXT UNIQUE, user_id TEXT, content_id TEXT,
                    content_version INTEGER, playback_id TEXT, seq INTEGER, type TEXT,
                    position_sec REAL, playback_rate REAL, client_ts REAL, received_at REAL);
                CREATE INDEX IF NOT EXISTS event_user_content_seq ON event(user_id, content_id, seq);
                CREATE TABLE IF NOT EXISTS listen_state (
                    user_id TEXT, content_id TEXT, last_position_sec REAL, played_sec REAL,
                    completed INTEGER, finished INTEGER, skipped INTEGER, liked INTEGER, saved INTEGER,
                    last_ts REAL, content_version INTEGER, duration_at_version REAL, base_version INTEGER,
                    prev_completed INTEGER, updated_at REAL, UNIQUE(user_id, content_id));
                CREATE TABLE IF NOT EXISTS ad_event (
                    id INTEGER PRIMARY KEY, event_id TEXT UNIQUE, user_id TEXT, ad_id TEXT,
                    playback_id TEXT, seq INTEGER, type TEXT, position_sec REAL, client_ts REAL, received_at REAL);
                CREATE INDEX IF NOT EXISTS ad_event_user_seq ON ad_event(user_id, seq);
                CREATE TABLE IF NOT EXISTS user_interest (
                    user_id TEXT UNIQUE, interests TEXT, updated_at REAL);
                CREATE TABLE IF NOT EXISTS topic_selection (
                    id INTEGER PRIMARY KEY, user_id TEXT, selection_id TEXT UNIQUE, interests TEXT, groups TEXT,
                    selected_at REAL, playback_started INTEGER DEFAULT 0, received_at REAL);
                CREATE INDEX IF NOT EXISTS topic_selection_user ON topic_selection(user_id, selected_at);
                CREATE TABLE IF NOT EXISTS user_routine (
                    user_id TEXT UNIQUE, routines TEXT, updated_at REAL);
            """)
            # 예전 events DB에도 새 열을 붙인다 (값이 없으면 NULL = 중립).
            columns = {row["name"] for row in db.execute("PRAGMA table_info(event)")}
            for name in ("skip_reason", "selection_id"):
                if name not in columns:
                    db.execute(f"ALTER TABLE event ADD COLUMN {name} TEXT")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def register_device(self, install_id, app_version, platform):
        user_id, token = "u_" + secrets.token_urlsafe(32), "t_" + secrets.token_urlsafe(32)
        now = time.time()
        with self._connect() as db:
            db.execute("INSERT INTO device (install_id,user_id,token,app_version,platform,created_at,last_seen_at) "
                       "VALUES (?,?,?,?,?,?,?)", (install_id, user_id, token, app_version, platform, now, now))
        return user_id, token

    def user_for_token(self, token):
        with self._connect() as db:
            row = db.execute("SELECT user_id FROM device WHERE token=?", (token,)).fetchone()
        return row["user_id"] if row else None

    def record(self, user_id, events, catalog):
        result = {"accepted": 0, "duplicate": 0, "rejected": []}
        touched = set()
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")  # 두 테이블에 걸친 중복 검사도 직렬화한다.
            for index, event in enumerate(events):
                e = event.model_dump()
                if db.execute("SELECT 1 FROM event WHERE event_id=? UNION ALL "
                              "SELECT 1 FROM ad_event WHERE event_id=?", (e["event_id"], e["event_id"])).fetchone():
                    result["duplicate"] += 1
                    continue
                if catalog.get(e["content_id"]) is None:
                    result["rejected"].append({"index": index, "event_id": e["event_id"], "reason": "unknown_content"})
                    continue
                e.update(user_id=user_id, client_ts=event.client_ts.timestamp(), received_at=now)
                if e["type"].startswith("ad_"):
                    table = "ad_event"
                    e["ad_id"] = e.pop("content_id")
                    del e["content_version"], e["playback_rate"], e["skip_reason"], e["selection_id"]
                else:
                    table = "event"
                    touched.add(e["content_id"])
                    if e["type"] != "skip":
                        e["skip_reason"] = None
                db.execute(f"INSERT INTO {table} ({','.join(e)}) VALUES ({','.join('?' for _ in e)})", tuple(e.values()))
                if table == "event" and e["type"] == "play" and e["selection_id"]:
                    db.execute("UPDATE topic_selection SET playback_started=1 WHERE user_id=? AND selection_id=?",
                               (user_id, e["selection_id"]))
                result["accepted"] += 1
            for content_id in sorted(touched):
                events = [dict(r) for r in db.execute(
                    "SELECT * FROM event WHERE user_id=? AND content_id=? ORDER BY seq,event_id", (user_id, content_id))]
                prior = db.execute("SELECT * FROM listen_state WHERE user_id=? AND content_id=?",
                                   (user_id, content_id)).fetchone()
                duration = catalog.get(content_id)["duration_sec"]
                base_version, prev_completed = (prior["base_version"], bool(prior["prev_completed"])) if prior else (1, False)
                version = max(e["content_version"] for e in events)
                if prior and version > prior["content_version"] and abs(duration - prior["duration_at_version"]) > 1.0:
                    base_version, prev_completed = version, bool(prior["completed"])
                # catalog에 version 열이 없어 catalog만 바뀐 경우의 규칙은 아직 적용하지 않는다.
                state = fold_content(events, duration, base_version, prev_completed)
                state.update(user_id=user_id, content_id=content_id, updated_at=now)
                columns = ','.join(state)
                updates = ','.join(f"{key}=excluded.{key}" for key in state if key not in {"user_id", "content_id"})
                db.execute(f"INSERT INTO listen_state ({columns}) VALUES ({','.join('?' for _ in state)}) "
                           f"ON CONFLICT(user_id,content_id) DO UPDATE SET {updates}", tuple(state.values()))
        return result

    def history(self, user_id, catalog):
        with self._connect() as db:
            rows = db.execute("SELECT content_id,last_position_sec,played_sec,completed,finished,skipped,liked,saved,last_ts "
                              "FROM listen_state WHERE user_id=? ORDER BY content_id", (user_id,)).fetchall()
        history = {}
        for row in rows:
            if catalog.get(row["content_id"]) is None:
                continue
            state = dict(row)
            for key in ("completed", "finished", "skipped", "liked", "saved"):
                state[key] = bool(state[key])
            state["last_ts"] = float(state["last_ts"] or 0.0)
            history[state["content_id"]] = state
        return history

    def record_selection(self, user_id, selection, groups):
        """골라 듣기 선택을 저장한다. 같은 id가 다시 오면 중복으로 센다. play가 먼저 와도 시작 여부를 맞춘다."""
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT user_id FROM topic_selection WHERE selection_id=?",
                             (selection.selection_id,)).fetchone()
            if row:
                return {"accepted": 0, "duplicate": 1}
            started = db.execute("SELECT 1 FROM event WHERE user_id=? AND selection_id=? AND type='play' LIMIT 1",
                                 (user_id, selection.selection_id)).fetchone() is not None
            db.execute("INSERT INTO topic_selection (user_id,selection_id,interests,groups,selected_at,playback_started,"
                       "received_at) VALUES (?,?,?,?,?,?,?)",
                       (user_id, selection.selection_id, json.dumps(selection.interests), json.dumps(sorted(groups)),
                        selection.selected_at.timestamp(), int(started), now))
        return {"accepted": 1, "duplicate": 0}

    def selections(self, user_id, now=None):
        """선택 이력 → playlist.selection_preferences.TopicSelection 목록 (30일 창)."""
        since = (now or time.time()) - EVENT_WINDOW_SEC
        with self._connect() as db:
            rows = db.execute("SELECT selection_id,groups,selected_at,playback_started FROM topic_selection "
                              "WHERE user_id=? AND selected_at>=? ORDER BY selected_at", (user_id, since)).fetchall()
        return [TopicSelection(selection_id=r["selection_id"],
                               selected_at=datetime.fromtimestamp(r["selected_at"], timezone.utc),
                               group_ids=tuple(json.loads(r["groups"])), playback_started=bool(r["playback_started"]))
                for r in rows]

    def playback_events(self, user_id, now=None):
        """넘김 계산(skip_preferences)에 쓰는 재생 이벤트 원본 (30일 창)."""
        since = (now or time.time()) - EVENT_WINDOW_SEC
        marks = ",".join("?" for _ in PLAYBACK_TYPES)
        with self._connect() as db:
            rows = db.execute("SELECT event_id,content_id,content_version,playback_id,seq,type,position_sec,"
                              f"playback_rate,client_ts,skip_reason FROM event WHERE user_id=? AND type IN ({marks}) "
                              "AND client_ts>=? ORDER BY content_id,seq,event_id",
                              (user_id, *PLAYBACK_TYPES, since)).fetchall()
        return [dict(r) for r in rows]

    def routines(self, user_id):
        with self._connect() as db:
            row = db.execute("SELECT routines,updated_at FROM user_routine WHERE user_id=?", (user_id,)).fetchone()
        return (json.loads(row["routines"]), row["updated_at"]) if row else (None, None)

    def set_routines(self, user_id, routines):
        now = time.time()
        with self._connect() as db:
            db.execute("INSERT INTO user_routine (user_id,routines,updated_at) VALUES (?,?,?) "
                       "ON CONFLICT(user_id) DO UPDATE SET routines=excluded.routines,updated_at=excluded.updated_at",
                       (user_id, json.dumps(routines, ensure_ascii=False), now))
        return now

    def set_interests(self, user_id, interests):
        now = time.time()
        with self._connect() as db:
            db.execute("INSERT INTO user_interest (user_id,interests,updated_at) VALUES (?,?,?) "
                       "ON CONFLICT(user_id) DO UPDATE SET interests=excluded.interests,updated_at=excluded.updated_at",
                       (user_id, json.dumps(interests, ensure_ascii=False), now))
        return {"interests": interests, "updated_at": now}

    def interests(self, user_id):
        return self.interests_with_time(user_id)["interests"]

    def interests_with_time(self, user_id):
        with self._connect() as db:
            row = db.execute("SELECT interests,updated_at FROM user_interest WHERE user_id=?", (user_id,)).fetchone()
        return {"interests": json.loads(row["interests"]), "updated_at": row["updated_at"]} if row else {
            "interests": [], "updated_at": None}
