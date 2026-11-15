"""스튜디오(관리 사이트)를 HTTP로만 부른다. 스튜디오 DB는 스튜디오만 쓴다."""
import json
import urllib.error
import urllib.parse
import urllib.request


class StudioError(RuntimeError):
    pass


class Studio:
    def __init__(self, url, token):
        self.url, self.token = url, token

    def _call(self, method, path, body=None, timeout=600):
        headers = {"X-Teum-Service": self.token}
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise StudioError(f"{method} {path.split('?')[0]} → {e.code}: {e.read()[:200].decode(errors='replace')}") from None
        except urllib.error.URLError as e:
            raise StudioError(f"스튜디오에 연결할 수 없습니다 ({self.url}): {e.reason}") from None

    def data(self):
        return json.loads(self._call("GET", "/admin/api/data"))

    def scripts(self):
        return {s["id"]: s for s in self.data()["scripts"]}

    def upload(self, name, raw):
        """대본 .json·.md, 결과 .csv 한 개 올리기. 사이트 화면의 올리기와 같은 처리"""
        out = json.loads(self._call("POST", "/admin/api/upload?name=" + urllib.parse.quote(name), raw))
        return out.get("results", [])

    def update(self, cid, field, value):
        return json.loads(self._call("POST", "/admin/api/update", {"tbl": "scripts", "id": cid, "field": field, "value": value}))

    def comment(self, cid, text):
        return json.loads(self._call("POST", "/admin/api/comment", {"tbl": "scripts", "id": cid, "text": text}))

    def add_to_app(self, ids):
        """음성 완료 대본을 승인하고 앱 카탈로그에 게시. 사이트 화면의 '앱에 추가'와 같은 처리"""
        return json.loads(self._call("POST", "/admin/api/add", {"ids": ids}))

    def review_kit(self):
        return self._call("GET", "/admin/kit/review.zip")
