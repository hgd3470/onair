"""파일과 SQLite로 관리하는 제작 상태."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import lameenc
import soundfile as sf


def now():
    return datetime.now(timezone.utc).isoformat()


def parse(path):
    """제작 정보와 낭독 블록만 추출한다."""
    path = Path(path)
    raw = path.read_text(encoding="utf-8-sig")
    sections = {int(n): body for n, body in re.findall(
        r"^## (\d+)\.[^\n]*\n(.*?)(?=^## |\Z)", raw, re.M | re.S)}
    meta = dict(re.findall(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*$", sections.get(1, ""), re.M))
    cid = meta.get("관리번호", "").lower()
    error = None if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", cid) else "관리번호 누락 또는 잘못된 형식"
    if error:
        cid = "invalid-" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
    block = re.search(r"^```text\s*\n(.*?)^```\s*$", sections.get(2, ""), re.M | re.S)
    lines = [s.strip() for s in block[1].splitlines() if s.strip()] if block else []
    if not lines:
        error = "; ".join(filter(None, [error, "빈 낭독 본문"]))
    title = re.search(r"^# (.+)$", raw, re.M)
    category = re.search(r"\((C\d+)\)", meta.get("관심사·프로그램", ""))
    duration = sum(int(n) * (60 if unit == "분" else 1) for n, unit in
                   re.findall(r"(\d+)\s*(분|초)", meta.get("목표 길이", "").split("·")[0]))
    source = re.search(r"\[([^\]]+)\]\(([^\s]+)\)", sections.get(4, ""))
    # 이용조건: 저작권 표시(©)가 있는 줄의 첫 문장, 없으면 '이용조건' 행의 값. 제작진 창작·요약 대본은 비워 둔다.
    notice = re.search(r"^[^\n|]*©[^\n]*", sections.get(4, ""), re.M)
    terms = re.search(r"^\|[^|\n]*이용조건\s*\|\s*([^|\n]+?)\s*\|", sections.get(4, ""), re.M)
    license = re.split(r"(?<=\.)\s", notice[0].strip())[0] if notice else (terms[1] if terms else None)
    # TTS가 잘못 읽기 쉬운 숫자·영문은 대본 단계에서 한글 읽기로 바꾼다. 막지 않고 경고만 남긴다.
    warnings = [f"{n}행: {line}" for n, line in enumerate(lines, 1) if re.search(r"[0-9A-Za-z]", line)]
    return dict(content_id=cid, title=title[1] if title else path.stem, warnings=warnings,
                category=category[1] if category else None, group=meta.get("대분류"),
                kind="story", type=meta.get("유형"), tags=meta.get("세부 태그", "").split(", ") if meta.get("세부 태그") else [],
                target_sec=duration, script_version=meta.get("대본 버전"), lines=lines,
                source=dict(title=source[1], url=source[2], license=license) if source else None,
                error=error)


class Pipeline:
    def __init__(self, work):
        self.work = Path(work).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.work / "pipeline.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS item (
                content_id TEXT PRIMARY KEY, title TEXT, state TEXT, attempts INTEGER DEFAULT 0,
                audio_version INTEGER DEFAULT 0, wav TEXT, duration_sec REAL, last_error TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS log (
                id INTEGER PRIMARY KEY, content_id TEXT, step TEXT, ok INTEGER, detail TEXT, at TEXT);
        """)

    def log(self, cid, step, ok=True, detail=""):
        self.db.execute("INSERT INTO log(content_id,step,ok,detail,at) VALUES (?,?,?,?,?)",
                        (cid, step, int(ok), detail, now()))

    def update(self, cid, **values):
        values["updated_at"] = now()
        self.db.execute("UPDATE item SET " + ",".join(f"{k}=?" for k in values) + " WHERE content_id=?",
                        (*values.values(), cid))

    def items(self):
        return self.db.execute("SELECT * FROM item ORDER BY content_id").fetchall()

    def item(self, cid):
        row = self.db.execute("SELECT * FROM item WHERE content_id=?", (cid,)).fetchone()
        if row is None:
            raise ValueError(f"알 수 없는 콘텐츠: {cid}")
        return row

    def script(self, cid):
        return json.loads((self.work / cid / "script.json").read_text(encoding="utf-8"))

    def ingest(self, files):
        for file in files:
            data = parse(file)
            cid = data["content_id"]
            folder = self.work / cid
            folder.mkdir(exist_ok=True)
            old = self.db.execute("SELECT * FROM item WHERE content_id=?", (cid,)).fetchone()
            if old and self.script(cid) == data and old["state"] not in ("rejected", "invalid"):
                self.log(cid, "ingest", detail="변경 없음")
                continue
            if old and old["state"] not in ("rejected", "invalid") and self.script(cid)["lines"] == data["lines"]:
                # 낭독 본문이 같으면 메타데이터(이용조건·분류 등)만 갱신하고 음원과 상태는 유지한다.
                (folder / "script.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                self.log(cid, "ingest", detail="메타데이터만 갱신")
                for warning in data["warnings"]:
                    self.log(cid, "ingest", detail="숫자·영문 한글 읽기 확인: " + warning)
                continue
            if old and old["state"] == "tts_pending":
                raise ValueError(f"진행 중인 TTS 결과를 먼저 처리하세요: {cid}")
            (folder / "script.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            (folder / "text.txt").write_text("\n".join(data["lines"]), encoding="utf-8")
            self.db.execute("INSERT OR IGNORE INTO item(content_id) VALUES (?)", (cid,))
            self.update(cid, title=data["title"], state="invalid" if data["error"] else "script_ready",
                        attempts=0, wav=None, duration_sec=None, last_error=data["error"])
            self.log(cid, "ingest", not data["error"], data["error"] or str(file))
            for warning in data["warnings"]:
                self.log(cid, "ingest", detail="숫자·영문 한글 읽기 확인: " + warning)

    def jobs(self, out, speaker, language):
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with out.open("a", encoding="utf-8") as stream:
            for row in self.items():
                if row["state"] not in ("script_ready", "tts_failed") or row["attempts"] >= 3:
                    continue
                cid = row["content_id"]
                version = row["audio_version"] + 1
                wav = str(self.work / cid / f"audio.v{version}.wav")
                job = dict(content_id=cid, text="\n".join(self.script(cid)["lines"]),
                           out_wav=wav, speaker=speaker, language=language)
                stream.write(json.dumps(job, ensure_ascii=False) + "\n")
                stream.flush()
                self.update(cid, state="tts_pending", audio_version=version, wav=wav, duration_sec=None)
                self.log(cid, "tts-jobs", detail=wav)
                count += 1
        if not count:
            self.log(None, "tts-jobs", detail="대상 없음")

    def results(self, path):
        self.log(None, "tts-ingest", detail=str(path))
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                result = json.loads(line)
                cid = result["content_id"]
                row = self.item(cid)
                if row["state"] != "tts_pending":
                    self.log(cid, "tts-ingest", False, "대기 중 아님: 중복 결과 무시")
                    continue
                if Path(result["wav"]).resolve() != Path(row["wav"]):
                    self.log(cid, "tts-ingest", False, "이전 버전 또는 경로 불일치")
                    continue
                if type(result["ok"]) is not bool:
                    raise ValueError("ok는 bool이어야 합니다")
                error = result.get("error") or "TTS 실패"
                if result["ok"]:
                    try:
                        duration = wav_duration(row["wav"])
                    except (OSError, RuntimeError, ValueError) as exc:
                        error = str(exc)
                    else:
                        self.update(cid, state="tts_done", duration_sec=duration, last_error=None)
                        self.log(cid, "tts-ingest", detail=row["wav"])
                        continue
                attempts = row["attempts"] + 1
                self.update(cid, state="failed" if attempts >= 3 else "tts_failed",
                            attempts=attempts, last_error=error)
                self.log(cid, "tts-ingest", False, error)
            except (ValueError, KeyError, TypeError) as exc:
                self.log(None, "tts-ingest", False, str(exc))

    def review(self, cid, approve, note):
        if self.item(cid)["state"] != "tts_done":
            raise ValueError("tts_done 상태만 검수할 수 있습니다")
        self.update(cid, state="approved" if approve else "rejected")
        self.log(cid, "review", detail=("승인: " if approve else "반려: ") + note)

    def redo(self, cid, note):
        """게시 뒤라도 품질 문제로 다시 합성한다. 음원 버전은 이어서 올라가고 이전 게시본은 유지된다."""
        if self.item(cid) is None:
            raise ValueError("모르는 content_id")
        self.update(cid, state="script_ready", attempts=0, last_error=None)
        self.log(cid, "redo", detail=note)

    def publish(self, root):
        root = Path(root).resolve() / "outputs"
        audio = root / "gen" / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        catalog = sqlite3.connect(root / "catalog.sqlite")
        try:
            catalog.executescript("""
                CREATE TABLE IF NOT EXISTS source (source_id TEXT PRIMARY KEY, license TEXT, attribution TEXT, url TEXT);
                CREATE TABLE IF NOT EXISTS script (script_id TEXT PRIMARY KEY, text TEXT);
                CREATE TABLE IF NOT EXISTS audio_asset (audio_id TEXT PRIMARY KEY, uri TEXT);
                CREATE TABLE IF NOT EXISTS content (content_id TEXT PRIMARY KEY, kind TEXT, title TEXT, category TEXT,
                    tags TEXT, duration_sec REAL, channel TEXT, play_no INTEGER, audio_id TEXT, script_id TEXT,
                    source_id TEXT, status TEXT);
            """)
            count = 0
            for row in self.items():
                if row["state"] not in ("approved", "published"):  # 게시본도 다시 쓰면 메타데이터(이용조건 등)가 갱신된다
                    continue
                cid = row["content_id"]
                data = self.script(cid)
                aid = f"{cid}.v{row['audio_version']}"
                target = audio / f"{aid}.mp3"
                duration = wav_duration(row["wav"])
                samples, sample_rate = sf.read(row["wav"], dtype="int16")
                if samples.ndim != 1:
                    raise ValueError("게시 음원은 mono여야 합니다")
                encoder = lameenc.Encoder()
                encoder.set_bit_rate(128)
                encoder.set_in_sample_rate(sample_rate)
                encoder.set_channels(1)
                encoder.set_quality(2)
                temporary = target.with_suffix(".mp3.tmp")
                temporary.write_bytes(encoder.encode(samples.tobytes()) + encoder.flush())
                temporary.replace(target)
                with catalog:
                    catalog.execute("INSERT INTO script VALUES (?,?) ON CONFLICT(script_id) DO UPDATE SET text=excluded.text",
                                    (cid, "\n".join(data["lines"])))
                    catalog.execute("INSERT INTO audio_asset VALUES (?,?) ON CONFLICT(audio_id) DO UPDATE SET uri=excluded.uri",
                                    (aid, f"/gen/{aid}.mp3"))
                    src = data["source"]
                    if src:
                        catalog.execute("INSERT INTO source VALUES (?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
                                        "license=excluded.license,attribution=excluded.attribution,url=excluded.url",
                                        (cid, src.get("license"), src["title"], src["url"]))
                    previous = catalog.execute("SELECT play_no, status FROM content WHERE content_id=?", (cid,)).fetchone()
                    # 새로 승인된 것만 공개로 바꾼다. 이미 게시된 항목을 다시 쓸 때는 카탈로그에서 정한 상태(보류 등)를 유지한다.
                    status = previous[1] if previous and row["state"] == "published" else "published"
                    play_no = previous[0] if previous else catalog.execute("SELECT COALESCE(MAX(play_no),0)+1 FROM content").fetchone()[0]
                    catalog.execute("""INSERT INTO content VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(content_id) DO UPDATE SET kind=excluded.kind,title=excluded.title,
                        category=excluded.category,tags=excluded.tags,duration_sec=excluded.duration_sec,
                        channel=excluded.channel,audio_id=excluded.audio_id,script_id=excluded.script_id,
                        source_id=excluded.source_id,status=excluded.status""",
                        (cid, data["kind"], data["title"], data["category"], json.dumps(data["tags"], ensure_ascii=False),
                         duration, "internal_demo", play_no, aid, cid, cid if src else None, status))
                self.update(cid, state="published", duration_sec=duration)
                self.log(cid, "publish", detail=str(target))
                count += 1
            if not count:
                self.log(None, "publish", detail="대상 없음")
        finally:
            catalog.close()

    def status(self):
        for row in self.items():
            print("\t".join(str(row[k]) for k in ("content_id", "title", "state", "attempts", "audio_version", "duration_sec", "last_error")))
        self.log(None, "status")


def wav_duration(path):
    """음원 헤더에서 실제 길이를 읽는다."""
    info = sf.info(str(path))
    if info.format not in ("WAV", "WAVEX") or info.frames <= 0:
        raise ValueError("비어 있거나 WAV가 아닌 음원")
    return info.frames / info.samplerate
