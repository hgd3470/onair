"""콘텐츠 카탈로그 (S1-03). 공개(published) 콘텐츠만 올리고 나머지는 없는 것으로 다룬다."""
import json
import sqlite3
from pathlib import Path


def load_published(db_path, channel=None):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    cols = {r[1] for r in db.execute("PRAGMA table_info(source)")}
    extra = ", src.license, src.attribution, src.url AS source_url" if "license" in cols else \
            ", NULL AS license, NULL AS attribution, NULL AS source_url"
    sql = ("SELECT c.content_id, c.kind, c.title, c.category, c.tags, c.duration_sec, c.channel, c.play_no, a.uri AS audio_uri, "
           "s.text AS script" + extra + " FROM content c JOIN audio_asset a ON a.audio_id=c.audio_id "
           "JOIN script s ON s.script_id=c.script_id LEFT JOIN source src ON src.source_id=c.source_id "
           "WHERE c.status='published'")
    rows = [dict(r) for r in db.execute(sql)]
    db.close()
    if channel == "public":
        rows = [r for r in rows if r["channel"] == "public"]
    for r in rows:
        r["tags"] = json.loads(r["tags"] or "[]")
    return rows


class Catalog:
    def __init__(self, rows, audio_dirs: dict[str, Path], api_prefix=""):
        self._files = {}  # 앱에 내주는 음원 주소 → 실제 파일
        self.items = []
        for r in rows:
            folder, name = r["audio_uri"].strip("/").split("/", 1)
            url = f"{api_prefix}/{folder}/{name}"
            self._files[url] = audio_dirs[folder] / name
            self.items.append({**r, "audio_uri": url})
        self.by_id = {c["content_id"]: c for c in self.items}

    @property
    def stories(self):
        return [c for c in self.items if c["kind"] == "story"]

    def get(self, content_id):
        return self.by_id.get(content_id)

    def audio_path(self, url):
        """공개 콘텐츠의 음원이 아니면 None. 파일이 있어도 내주지 않는다."""
        return self._files.get(url)

    @staticmethod
    def public_view(c):
        return {k: c.get(k) for k in ("content_id", "kind", "title", "category", "tags", "duration_sec", "audio_uri",
                                      "license", "attribution", "source_url")}
