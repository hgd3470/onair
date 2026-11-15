"""자동화가 기억해야 하는 것만 파일 하나에 둔다: 대본마다 올린 판, 검수한 판, 수정 왕복 횟수."""
import json
import os
import time
from pathlib import Path


class State:
    def __init__(self, path: Path):
        self.path = path
        self.d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": {}}

    def item(self, cid):
        return self.d["items"].setdefault(cid, {"own": False, "rounds": 0})

    def get(self, cid):
        return self.d["items"].get(cid)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def note(self, cid, **kw):
        it = self.item(cid)
        it.update(kw, ts=time.time())
        return it
