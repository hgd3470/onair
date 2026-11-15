"""롱폼(두 사람 대담) 제작 파이프라인.

python ai/longform/pipeline.py status            → 기획마다 어느 단계까지 왔는지
python ai/longform/pipeline.py run [ID ...]      → 기획을 끝까지 돌린다(ID를 안 주면 끝나지 않은 기획 전부)
python ai/longform/pipeline.py run ID --voice    → 음성까지 만든다(Typecast 크레딧이 나간다)
python ai/longform/pipeline.py new ID            → 빈 기획 파일을 만든다

기획: $TEUM_HOME/longform/plans/<ID>.toml
  host_a, host_b      진행자 두 사람 (A가 아는 사람, B가 묻는 사람)
  hook                B가 던지는 첫 질문
  concept             프로그램 성격·말투
  research            자료 조사 지시. 비우고 sources/<ID>.md를 사람이 넣어도 된다
  title               정해 두면 모델이 바꾸지 않는다. 비우면 모델이 후보 중에서 고른다
  title_style         제목 규칙(비우면 일상 질문형)
  length              대사 글자 수 [최소, 최대]
  voice_a, voice_b    Typecast voice_id. 음성 단계에 쓴다

단계(모델은 생성기와 같은 설정: 작성=Gemini, 검토=GPT, 키는 TEUM_SCRIPT_AGENT_ENV 파일)
  ① 자료 조사   Gemini가 구글 검색으로 출처를 찾고, 수집기와 같은 규칙(robots 준수)으로 원문을 받는다
  ② 사실 카드   Gemini가 원문에서 카드를 뽑는다. 카드마다 원문 구절을 붙이고, 그 구절이 원문에 실제로 있는지 기계로 대조한 뒤
                GPT가 카드 문장이 구절과 맞는지 다시 본다. 둘 중 하나라도 어긋나면 버린다
  ③ 대본       Gemini 작성 → GPT 카드 대조 검토 → Gemini 수정 (최대 3바퀴)
  ④ 제목       Gemini가 후보 6개를 내고 고른다
  ⑤ 음성       --voice일 때만. 대사마다 A/B 목소리로 합성해 하나로 잇는다
단계마다 결과 파일이 남고, 이미 끝난 단계는 건너뛴다. 키 값은 찍지 않는다.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
import longform as L  # noqa: E402

REPO = L.REPO
sys.path.insert(0, str(REPO / "ai" / "issue_collector" / "src"))
sys.path.insert(0, str(REPO / "services" / "studio"))
try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib

ROOT = L.HOME / "longform"
PLANS, SOURCES, OUT, WORK = ROOT / "plans", ROOT / "sources", ROOT / "out", ROOT / "research"
MIN_CARDS = 12

PLAN_TEMPLATE = '''host_a = "아는 사람 (예: 헬스 트레이너)"
host_b = "묻는 사람 (예: 의욕만 넘치는 헬린이)"
hook = "B가 던지는 첫 질문"
concept = "프로그램 성격과 말투"
research = """
무엇을 조사할지. 다룰 개념, 꼭 넣을 수치, 믿을 만한 출처 종류(공공기관·원 논문 등)를 적는다.
"""
title = ""
title_style = ""
length = [3600, 4400]
voice_a = ""
voice_b = ""
'''


# ---------- 공통 ----------
def models():
    cfg = tomllib.loads((REPO / "ai" / "script_agent" / "config.toml").read_text())["models"]
    k = L.keys()
    return cfg, k, L.APIProvider(cfg["generator"], k, 600), L.APIProvider(cfg["reviewer"], k, 600)


def plan_of(pid):
    p = tomllib.loads((PLANS / f"{pid}.toml").read_text(encoding="utf-8"))
    p["id"] = pid
    return p


def stage(pid):
    """기획이 어디까지 왔는지: 기획 → 조사 → 카드 → 대본 → 음성"""
    out = OUT / pid
    if (out / "episode.mp3").exists():
        return "음성 완료"
    if (out / "script.json").exists():
        return "대본 완료"
    if (SOURCES / f"{pid}.md").exists():
        return "카드 완료"
    if (WORK / pid / "pages.json").exists():
        return "조사 완료"
    return "기획"


def log(pid, **kw):
    (OUT / pid).mkdir(parents=True, exist_ok=True)
    with open(OUT / pid / "pipeline.log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **kw}, ensure_ascii=False) + "\n")


# ---------- ① 자료 조사 ----------
class Queries(BaseModel):
    queries: list[str]


QUERIES = """너는 팟캐스트 자료 조사원이다. 조사 지시를 읽고 구글 검색어 4~6개를 만든다.
공공기관·원 논문·교과서·공식 통계를 찾을 검색어를 우선한다. 한국 자료가 필요한 것은 한국어로, 논문은 영어로 쓴다."""


def grounded_search(gemini_key, model, query):
    """Gemini 구글 검색 도구로 찾은 출처 URL(검색 근거에 잡힌 것만)"""
    body = {"contents": [{"role": "user", "parts": [{"text": f"다음을 조사할 원문 출처를 검색하라. 제목과 URL만 나열하라. 기억으로 URL을 만들지 마라: {query}"}]}],
            "tools": [{"google_search": {}}], "generationConfig": {"maxOutputTokens": 4000}}
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                   headers={"x-goog-api-key": gemini_key}, json=body, timeout=180)
    if r.is_error:
        raise RuntimeError(f"search_http_{r.status_code}")
    c = (r.json().get("candidates") or [{}])[0]
    chunks = (c.get("groundingMetadata") or {}).get("groundingChunks") or []
    return [{"url": x["web"]["uri"], "title": x["web"].get("title", "")} for x in chunks if x.get("web", {}).get("uri")]


UA = "teum-longform/1 (research; respects robots.txt)"


class Fetcher:
    """수집기와 같은 안전 규칙: http(s)만, 공인 주소만, robots.txt 준수, 4MB 상한, 리디렉트마다 다시 확인.
    수집기 Fetcher는 등록된 언론사만 받으므로 롱폼(논문·공공기관·백과)용으로 따로 둔다. 받은 원문은 cache에 남긴다"""

    def __init__(self, cache):
        import urllib.robotparser  # noqa: F401
        self.cache, self.robots = Path(cache), {}
        self.cache.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def safe(url):
        import ipaddress
        import socket
        from urllib.parse import urlsplit
        u = urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.port not in (None, 80, 443):
            raise ValueError("unsafe_url")
        for row in socket.getaddrinfo(u.hostname, u.port or 443):
            if not ipaddress.ip_address(row[4][0]).is_global:
                raise ValueError("nonpublic_address")

    def allowed(self, url):
        import urllib.robotparser
        from urllib.parse import urlsplit
        u = urlsplit(url)
        origin = f"{u.scheme}://{u.netloc}"
        if origin not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = httpx.get(origin + "/robots.txt", headers={"User-Agent": UA}, timeout=15, follow_redirects=True)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
                self.robots[origin] = rp if r.status_code in (200, 404, 410) else False
            except httpx.HTTPError:
                self.robots[origin] = False
        rp = self.robots[origin]
        return bool(rp) and rp.can_fetch(UA, url)

    def get(self, url):
        import hashlib
        from urllib.parse import urljoin, urlsplit
        key = self.cache / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".json")
        if key.exists():
            d = json.loads(key.read_text())
            return d["url"], d["text"]
        for _ in range(6):
            self.safe(url)
            if urlsplit(url).hostname != "vertexaisearch.cloud.google.com" and not self.allowed(url):
                raise ValueError("robots_denied")
            with httpx.stream("GET", url, headers={"User-Agent": UA}, timeout=25) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    raise ValueError(f"http_{r.status_code}")
                data = bytearray()
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > 4_000_000:
                        raise ValueError("too_large")
                text = bytes(data).decode(r.encoding or "utf-8", errors="replace")
            key.write_text(json.dumps({"url": url, "text": text}, ensure_ascii=False))
            return url, text
        raise ValueError("redirect_limit")


def page_text(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer", "noscript", "form"]):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def research(pid, plan):
    cfg, k, writer, _ = models()
    folder = WORK / pid
    folder.mkdir(parents=True, exist_ok=True)
    qs = writer.complete(L.with_prefs(QUERIES, L.prefs_for(pid, "research")), {"research": plan["research"], "hook": plan["hook"]}, Queries).value.queries[:6]
    found = []
    for q in qs:
        try:
            found += grounded_search(k["gemini_api_key"], cfg["generator"]["model"], q)
        except RuntimeError as e:
            log(pid, stage="조사", query=q, error=str(e))
    fetcher = Fetcher(folder / "cache")
    pages, seen = [], set()
    for f in found:
        if len(pages) >= 14:
            break
        try:
            url, html = fetcher.get(f["url"])  # 검색 근거 주소는 리디렉트라 원문 주소로 풀린다
        except Exception as e:  # robots 거부·403·너무 큼 등은 건너뛴다
            log(pid, stage="조사", url=f["url"][:120], skip=str(e)[:80])
            continue
        if url in seen or html[:5] == "%PDF-":
            continue
        text = page_text(html)
        if len(text) < 800:
            continue
        seen.add(url)
        pages.append({"id": f"S{len(pages) + 1}", "url": url, "title": f["title"], "text": text[:30000]})
    (folder / "pages.json").write_text(json.dumps({"queries": qs, "pages": pages}, ensure_ascii=False, indent=1))
    log(pid, stage="조사", queries=len(qs), found=len(found), pages=len(pages))
    if len(pages) < 3:
        raise RuntimeError(f"원문을 {len(pages)}개밖에 못 받았습니다. research 지시를 고치거나 sources/{pid}.md를 직접 넣으세요")


# ---------- ② 사실 카드 ----------
class Src(BaseModel):
    id: str
    title: str
    publisher: str
    date: str
    usage: str


class Card(BaseModel):
    id: str
    text: str
    source_id: str
    quote: str


class Cards(BaseModel):
    title_idea: str
    sources: list[Src]
    facts: list[Card]
    cautions: list[str]


class Verdict(BaseModel):
    id: str
    ok: bool
    why: str


class Verdicts(BaseModel):
    results: list[Verdict]


CARDS = """너는 팟캐스트 사실 확인 담당이다. 입력 pages(원문)만 근거로 사실 카드 20~28장을 만든다.
- 카드 하나에 사실 하나. 한국어로 짧게 풀어 쓰고 숫자·단위·연도·조건·표본 수는 원문 그대로 둔다.
- quote에는 그 카드를 뒷받침하는 원문 구절을 한 글자도 바꾸지 않고 20~200자로 복사한다(원문 언어 그대로).
- 원문에 없는 내용, 기억으로 아는 내용은 카드로 만들지 않는다. 예외·한계·반대 결과도 별도 카드로 만든다.
- sources에는 실제로 카드를 뽑은 page의 id(S1…)와 제목·발행처·날짜(모르면 '미상')·이용 메모(공공누리/논문 사실만/기사 사실만 등)를 적는다.
- cautions에는 대본이 사실처럼 말하면 안 되는 흔한 오해·과장·불확실한 점을 적는다.
조사 지시와 첫 질문을 보고 대담에 필요한 사실을 고르게 뽑는다."""

VERIFY = """너는 사실 대조 검토자다. 각 카드의 text가 quote(원문 구절)만으로 뒷받침되는지 본다.
숫자·단위·조건이 다르거나, quote보다 넓게 일반화했거나, quote에 없는 내용을 더했으면 ok=false. 번역·요약 차이는 괜찮다."""


def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def cards(pid, plan):
    _, _, writer, checker = models()
    pages = json.loads((WORK / pid / "pages.json").read_text())["pages"]
    c = writer.complete(L.with_prefs(CARDS, L.prefs_for(pid, "research")), {"research": plan["research"], "hook": plan["hook"],
                                "pages": [{"id": p["id"], "url": p["url"], "title": p["title"], "text": p["text"]} for p in pages]}, Cards).value
    by = {p["id"]: norm(p["text"]) for p in pages}
    kept = [x for x in c.facts if x.source_id in by and len(norm(x.quote)) >= 15 and norm(x.quote) in by[x.source_id]]
    dropped = {"quote_not_found": [x.id for x in c.facts if x not in kept]}
    if kept:
        v = checker.complete(VERIFY, {"cards": [x.model_dump() for x in kept]}, Verdicts).value
        bad = {r.id: r.why for r in v.results if not r.ok}
        dropped["not_supported"] = bad
        kept = [x for x in kept if x.id not in bad]
    (WORK / pid / "cards.json").write_text(json.dumps({"cards": c.model_dump(), "kept": [x.id for x in kept], "dropped": dropped}, ensure_ascii=False, indent=1))
    log(pid, stage="카드", made=len(c.facts), kept=len(kept), dropped=dropped)
    if len(kept) < MIN_CARDS:
        raise RuntimeError(f"검증을 통과한 카드가 {len(kept)}장뿐입니다(최소 {MIN_CARDS}장)")
    url = {p["id"]: p["url"] for p in pages}
    used = {x.source_id for x in kept}
    md = [f"# {c.title_idea}", f"질문: {plan['hook']}", "", "## 출처"]
    md += [f"- {s.id}. {s.title} · {s.publisher} · {s.date} · {url.get(s.id, '')} · {s.usage}" for s in c.sources if s.id in used]
    md += ["", "## 사실 카드"] + [f"- F{n}. {x.text} [{x.source_id}]" for n, x in enumerate(kept, 1)]
    md += ["", "## 주의"] + [f"- {x}" for x in c.cautions]
    SOURCES.mkdir(parents=True, exist_ok=True)
    (SOURCES / f"{pid}.md").write_text("\n".join(md) + "\n", encoding="utf-8")


# ---------- ③④ 대본·제목 ----------
def script(pid, plan):
    r = L.run(str(SOURCES / f"{pid}.md"), rounds=3, plan=plan)
    log(pid, stage="대본", **r)
    return r


# ---------- ⑤ 음성 ----------
def voice(pid, plan):
    import typecast_client as tc
    if not (plan.get("voice_a") and plan.get("voice_b")):
        raise RuntimeError("기획에 voice_a, voice_b(Typecast voice_id)를 넣어야 음성을 만듭니다")
    j = json.loads((OUT / pid / "script.json").read_text())
    lines = j["script"]["lines"]
    parts = OUT / pid / "parts"
    parts.mkdir(exist_ok=True)
    for n, x in enumerate(lines):
        f = parts / f"{n:03d}{x['speaker']}.mp3"
        if f.exists():
            continue
        req = {"voice_id": plan["voice_a" if x["speaker"] == "A" else "voice_b"], "text": x["text"], "model": tc.MODEL,
               "language": "KOR", "prompt": {"emotion_type": "smart"},
               "output": {"audio_format": "mp3", "audio_tempo": 1.0, "target_lufs": -16}}
        audio = tc.call("POST", "/v1/text-to-speech", req, soft=True)
        if audio is None:
            req["prompt"] = {"emotion_type": "preset", "emotion_preset": "normal"}
            audio = tc.call("POST", "/v1/text-to-speech", req)
        f.write_bytes(audio)
    ff = os.environ.get("TEUM_FFMPEG", "ffmpeg")
    with tempfile.TemporaryDirectory() as t:
        gap = Path(t) / "gap.mp3"
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "0.35", "-q:a", "4", str(gap)], check=True)
        lst = Path(t) / "list.txt"
        lst.write_text("".join(f"file '{p}'\nfile '{gap}'\n" for p in sorted(parts.glob("*.mp3"))))
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "44100", "-ac", "1", "-b:a", "128k",
                        str(OUT / pid / "episode.mp3")], check=True)
    timings(pid)
    log(pid, stage="음성", lines=len(lines), chars=sum(len(x["text"]) for x in lines))


# ---------- 다시 만들기 (스튜디오 버튼 → requests/<ID>.json → watch) ----------
REQ = ROOT / "requests"


def redo(pid, frm):
    """frm: script(대본부터, 음성이 있었으면 음성까지) | title(제목만) | voice(음성만). 지난 결과는 out/<ID>/history/<시각>/에 남긴다"""
    import shutil
    out = OUT / pid
    had_voice = (out / "episode.mp3").exists()
    hist = out / "history" / time.strftime("%Y%m%d-%H%M%S")
    hist.mkdir(parents=True, exist_ok=True)
    for name in ("script.json", "script.md", "episode.mp3", "usage.json"):
        if (out / name).exists():
            shutil.copy2(out / name, hist / name)
    plan = plan_of(pid)
    if frm == "title":
        j = json.loads((out / "script.json").read_text())
        sc = L.Script.model_validate(j["script"])
        if plan.get("title"):
            sc.title, opts = plan["title"], []
        else:
            sc.title, opts = L.title_for(pid, j["show"], sc, plan.get("title_style"))
        j["script"], j["title_options"] = sc.model_dump(), opts
        j["prefs_used"] = [p for p in j.get("prefs_used", []) if p.get("kind") != "title"] + L.prefs_for(pid, "title")
        (out / "script.json").write_text(json.dumps(j, ensure_ascii=False, indent=1))
        log(pid, stage="제목 다시", title=sc.title)
        return
    if frm in ("script", "voice"):
        (out / "episode.mp3").unlink(missing_ok=True)
        shutil.rmtree(out / "parts", ignore_errors=True)
    if frm == "script":
        for name in ("script.json", "script.md"):
            (out / name).unlink(missing_ok=True)
        for f in out.glob("[0-9][0-9]-*.json"):  # 단계별 중간 결과는 history로 옮긴다
            shutil.move(str(f), hist / f.name)
    run(pid, with_voice=had_voice or frm == "voice")
    log(pid, stage="다시 만들기", frm=frm)


def watch():
    """스튜디오가 남긴 다시 만들기 요청을 하나씩 처리한다(teum_up이 띄운다)"""
    REQ.mkdir(parents=True, exist_ok=True)
    while True:
        for f in sorted(REQ.glob("*.json")):
            try:
                r = json.loads(f.read_text())
                pid = f.stem
                (REQ / f"{pid}.running").write_text(json.dumps(r))
                f.unlink()
                print(json.dumps({"redo": pid, **r}, ensure_ascii=False), flush=True)
                redo(pid, r.get("from", "script"))
            except Exception as e:  # 요청 하나가 실패해도 다음 요청은 처리한다
                log(f.stem, error=f"다시 만들기 실패: {type(e).__name__}: {e}"[:300])
            finally:
                (REQ / f"{f.stem}.running").unlink(missing_ok=True)
        time.sleep(10)


def timings(pid, gap=0.35):
    """대사별 시작·끝 초(앱이 듣는 위치에 맞춰 대본을 따라가게). parts/*.mp3 길이 + 사이 쉼"""
    ffprobe = os.environ.get("TEUM_FFPROBE", "ffprobe")
    t, rows = 0.0, []
    for f in sorted((OUT / pid / "parts").glob("*.mp3")):
        d = float(subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                                 capture_output=True, text=True, check=True).stdout.strip() or 0)
        rows.append({"start": round(t, 2), "end": round(t + d, 2)})
        t += d + gap
    (OUT / pid / "episode.json").write_text(json.dumps({"duration_sec": round(t, 1), "lines": rows}, ensure_ascii=False))
    return rows


# ---------- 실행 ----------
def run(pid, with_voice=False):
    plan = plan_of(pid)
    if stage(pid) == "기획" and not (SOURCES / f"{pid}.md").exists():
        research(pid, plan)
    if not (SOURCES / f"{pid}.md").exists():
        cards(pid, plan)
    if not (OUT / pid / "script.json").exists():
        script(pid, plan)
    if with_voice and not (OUT / pid / "episode.mp3").exists():
        voice(pid, plan)
    return stage(pid)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    ids = [a for a in sys.argv[2:] if not a.startswith("--")]
    PLANS.mkdir(parents=True, exist_ok=True)
    if cmd == "new":
        for pid in ids:
            f = PLANS / f"{pid}.toml"
            if not f.exists():
                f.write_text(PLAN_TEMPLATE, encoding="utf-8")
            print(f)
        return
    if cmd == "watch":
        return watch()
    if cmd == "timings":  # 이미 만든 음성의 대사 위치를 다시 잰다
        for pid in ids:
            print(pid, len(timings(pid)))
        return
    plans = sorted(p.stem for p in PLANS.glob("*.toml"))
    if cmd == "status":
        for pid in plans:
            print(f"{pid}\t{stage(pid)}")
        return
    if cmd == "run":
        for pid in ids or [p for p in plans if stage(p) != "음성 완료" and stage(p) != "대본 완료"]:
            try:
                print(json.dumps({"id": pid, "stage": run(pid, "--voice" in sys.argv)}, ensure_ascii=False), flush=True)
            except Exception as e:  # 한 기획이 막혀도 다음 기획은 돈다
                log(pid, error=f"{type(e).__name__}: {e}"[:300])
                print(json.dumps({"id": pid, "error": f"{type(e).__name__}: {e}"[:300]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
