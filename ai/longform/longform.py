"""두 사람 대담형 롱폼 팟캐스트 대본 (시험판).

python ai/longform/longform.py <소스.md> [...]   → TEUM_HOME/longform/out/<이름>/ 에 대본·검토 결과

흐름: 사실 카드(소스 파일) → Gemini가 대본 작성 → GPT가 카드 대조 검토 → 문제가 있으면 Gemini가 한 번 고침 → GPT 재검토.
모델과 키는 생성기(script_agent)와 같다: config.toml의 generator/reviewer, 키는 TEUM_SCRIPT_AGENT_ENV 파일.
키 값은 찍지 않는다.
"""
import json
import os
import sys
import time
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ai" / "script_agent" / "src"))
from script_agent.providers import APIProvider, ProviderError  # noqa: E402

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib

HOME = Path(os.environ.get("TEUM_HOME", REPO / "var"))
OUT = HOME / "longform" / "out"

# 회차마다 두 사람의 역할과 첫 질문. 소스 파일 이름 앞 숫자로 고른다
SHOWS = {
    "0": ("시사 기자", "뉴스 안 보는 친구", "근데 그게 내 월급이랑 무슨 상관인데?",
          "오늘의 이슈 하나를 배경부터 일상에 미치는 영향까지 풀어 준다"),
    "1": ("헬스 트레이너", "의욕만 넘치는 헬린이", "어제 치킨 먹었는데 오늘 두 배로 운동하면 되죠?",
          "초보자의 흔한 착각을 질문으로 던지고 운동과 식단의 원리를 설명한다"),
    "2": ("자연어처리 교수", "끝까지 '왜요?' 하는 대학원생", "교수님, 단어 벡터는 왜 빼고 더하면 의미 계산이 되는 거예요?",
          "개념 하나를 비유에서 시작해 원리까지 파고드는 강의형 대화. 진짜 전공 강의 수준으로 간다: 교수는 원-핫, 동시출현 행렬, "
          "PPMI, 코사인 유사도, 스킵그램, 네거티브 샘플링, 셀프 어텐션 같은 실제 용어를 정확히 쓰고, 학생이 '왜요?'로 파고들면 "
          "수식과 학습 목표를 말로 풀어(무엇을 곱하고 무엇을 높이고 낮추는지) 설명한다. 비유는 도입에만 쓰고, 연구자 이름·연도와 "
          "실험 결과, 알려진 한계, 아직 결론 나지 않은 논쟁까지 다룬다. 쉬운 말로 뭉개지 않는다. 다루는 분야는 단어 표현 학습(워드 임베딩)에서 "
          "맥락 임베딩까지 하나로 좁혀 깊게 간다. 제목도 대중 질문이 아니라 대학원 세미나 제목처럼 분야 용어로 정확하게 짓는다"),
    "3": ("역사학자", "그 시대에 떨어진 현대인", "조선에 왔는데 일단 취업은 어떻게 해요?",
          "먹고살기 같은 생활 질문으로 그 시대를 탐험한다"),
    "4": ("심리학 전공한 친구", "MBTI 과몰입 친구", "우리 MBTI 궁합 최악이래, 그럼 헤어져야 돼?",
          "여자 친구 둘이 카페에서 수다 떨듯 주고받는 연애 심리 토크. 반말로 티키타카하고 웃기게 받아치되, 심리학 친구는 "
          "연구 결과와 숫자로 근거를 댄다. MBTI 친구는 진심으로 과몰입해서 반박하고 자기 연애 얘기를 꺼낸다. MBTI를 비웃거나 "
          "깎아내리지 않고 '어디까지 믿어도 되는지'를 같이 따진다. 마지막엔 진짜로 연애에 도움 되는 연구 결과로 마무리한다"),
}

WRITE = """너는 한국어 오디오 팟캐스트 작가다. 차 안에서 귀로만 듣는 두 사람 대담 대본을 쓴다.
A는 {a}, B는 {b}다. 프로그램 성격: {concept}.
첫 질문은 B가 던진다: "{hook}"

사실 규칙
- 사실·숫자·날짜·인용은 입력 facts 카드에 있는 것만 쓴다. 각 대사의 fact_ids에 근거 카드 ID를 적는다. 사실 주장이 없는 대사(감탄, 질문, 비유, 연결)는 fact_ids를 비운다.
- 카드에 없는 숫자·사례·연구·인물 발언을 만들지 않는다. 카드의 조건·예외·기준일을 빠뜨리지 않는다.
- cautions에 적힌 내용은 사실처럼 말하지 않는다. B가 흔한 착각을 말하면 A가 카드 근거로 바로잡는다.
- 출처 문장을 그대로 옮기지 않고 말로 풀어 쓴다. 비유는 '비유'임이 드러나게 쓴다.
- 의료·법률·투자는 일반 정보로만 말하고 개인 처방이나 단정은 하지 않는다.

말하기 규칙
- 실제로 두 사람이 수다 떨듯 주고받는다. B는 듣는 사람 입장에서 엉뚱하지만 그럴듯한 질문, 맞장구, 되묻기를 한다. A는 쉬운 비유로 시작해 원리까지 간다.
- 한 대사는 1~3문장, 문장 하나는 60자 이하. 숫자는 소리 나는 대로 한글로 쓴다(예: 만 오천 원, 이천이십육 년). 괄호·기호·영문 약어 남발 금지(필요하면 한글 읽기를 함께).
- 구성: 오프닝(질문과 오늘 다룰 것) → 배경 → 핵심 원리 → 내 생활과의 연결 → B가 오늘 배운 것을 자기 말로 정리 → A가 한 줄 마무리와 '오늘 내용은 어떤 자료를 바탕으로 했는지' 출처 이름 소개.
- 분량: 대사 글자 수 합계 {min_chars}~{max_chars}자.
- 운전 중 조작이나 메모를 요구하지 않는다.
"""

CHECK = """너는 팟캐스트 대본 사실 검토자다. 입력 facts 카드와 cautions만 근거로 대본 lines를 검토한다.
다음을 issues로 낸다(line은 lines 배열의 0부터 시작하는 번호):
- unsupported: 카드에 없는 사실·숫자·사례를 말함, 또는 fact_ids가 그 주장을 뒷받침하지 않음
- number: 숫자·단위·날짜·조건이 카드와 다름(한글 읽기 표기 차이는 문제 아님)
- caution: cautions에 적힌 내용을 사실처럼 말함, 의료·법률·투자를 단정함
- copy: 출처 문장을 거의 그대로 옮긴 듯함
- listen: 귀로 듣기에 너무 길거나 헷갈리는 문장, 기호·영문 남발
문제가 없으면 issues를 비우고 ok=true. fix에는 고친 대사 문장을 제안한다. 사소한 문체 취향은 내지 않는다."""

REVISE = WRITE + """
지금은 수정 단계다. 입력 draft와 issues를 보고 문제 대사만 고쳐 전체 대본을 다시 낸다. 문제없는 대사는 그대로 둔다."""


class Line(BaseModel):
    speaker: Literal["A", "B"]
    text: str
    fact_ids: list[str]


class Script(BaseModel):
    title: str = Field(description="회차 제목. 짧고 궁금하게")
    lines: list[Line]


class Titles(BaseModel):
    options: list[str]
    best: int = Field(description="options 중 가장 좋은 것의 0부터 시작하는 번호")


TITLE = """너는 팟캐스트 제목 카피라이터다. 완성된 대본을 읽고 제목 후보 6개를 낸 뒤 가장 좋은 하나를 고른다.
청취자가 제목만 보고 무슨 얘기인지 바로 알고 듣고 싶어져야 한다. 대본 내용과 사실 범위를 넘지 않는다.
{style}"""
TITLE_STYLE = {
    "2": "대학원 세미나 제목처럼 분야 용어를 정확히 쓴다. 과장·낚시 금지.",
}
TITLE_DEFAULT = ('일상 구어체 질문 한 문장, 20자 안팎. 좋은 예: "치킨 한마리 먹고 얼마나 운동하면 살이 안찔까?" '
                 "전문용어·약어·정책 이름·영문은 쓰지 않거나 풀어 쓴다. 쉼표로 두 토막 내지 않는다.")


def title_for(name, show, script, style=None):
    """파이프라인 생성 모델(Gemini)이 제목 후보를 내고 하나를 고른다"""
    cfg = tomllib.loads((REPO / "ai" / "script_agent" / "config.toml").read_text())["models"]
    t = APIProvider(cfg["generator"], keys(), 300).complete(
        with_prefs(TITLE.format(style=style or TITLE_STYLE.get(name[0], TITLE_DEFAULT)), prefs_for(name, "title")),
        {"show": list(show), "script": "\n".join(f"{x.speaker}: {x.text}" for x in script.lines)[:8000]}, Titles).value
    return t.options[t.best if 0 <= t.best < len(t.options) else 0], t.options


class Issue(BaseModel):
    line: int
    kind: Literal["unsupported", "number", "caution", "copy", "listen"]
    problem: str
    fix: str


class Check(BaseModel):
    ok: bool
    issues: list[Issue]


def parse_source(path):
    """소스 md에서 출처·사실 카드·주의 목록을 읽는다"""
    sec, out = None, {"title": "", "sources": [], "facts": [], "cautions": []}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("# ") and not out["title"]:
            out["title"] = line[2:].strip()
        elif line.startswith("## "):
            sec = {"출처": "sources", "사실 카드": "facts", "주의": "cautions"}.get(line[3:].strip())
        elif sec and line[:1] in "-*" and len(line) > 2:
            out[sec].append(line[1:].strip())
    return out


# ---- 제작진 선호 (스튜디오 '롱폼' 탭에서 남긴 의견) ----
# $TEUM_HOME/longform/prefs.json: [{"id","text","kind": all|script|title|research,"show": ""(전체)|회차 ID,"by","at","active"}]
PREFS = HOME / "longform" / "prefs.json"


def prefs_for(pid, kind):
    """이 회차·이 단계에 걸리는 켜진 선호. 프롬프트에 붙일 목록"""
    try:
        rows = json.loads(PREFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in rows if r.get("active", True) and r.get("show", "") in ("", pid) and r.get("kind", "all") in ("all", kind)]


def with_prefs(system, rows):
    if not rows:
        return system
    return system + "\n\n제작진 선호 (아래를 반드시 반영한다. 단, 사실 규칙과 충돌하면 사실 규칙이 이긴다):\n" + "\n".join(f"- {r['text']}" for r in rows)


def keys():
    f = os.environ.get("TEUM_SCRIPT_AGENT_ENV") or dotenv_values(HOME / "teum.env").get("TEUM_SCRIPT_AGENT_ENV") or str(HOME / "script_agent.env")
    return {k.lower(): v for k, v in dotenv_values(f, interpolate=False).items() if v}


def chars(script):
    return sum(len(x.text) for x in script.lines)


def to_md(show, src, script, check):
    a, b = show[0], show[1]
    body = "\n\n".join(f"**{'A' if x.speaker == 'A' else 'B'} ({a if x.speaker == 'A' else b})** {x.text}"
                       + (f"  <sub>{', '.join(x.fact_ids)}</sub>" if x.fact_ids else "") for x in script.lines)
    issues = "\n".join(f"- {i.line}번 대사 [{i.kind}] {i.problem}" for i in check.issues) or "- 없음"
    return (f"# {script.title}\n\nA: {a} · B: {b} · 대사 {len(script.lines)}개 · {chars(script):,}자 "
            f"(약 {chars(script) / 8.3 / 60:.1f}분)\n\n{body}\n\n## 최종 검토에서 남은 지적\n{issues}\n\n## 출처\n"
            + "\n".join(f"- {s}" for s in src["sources"]) + "\n\n## 사실 카드\n" + "\n".join(f"- {f}" for f in src["facts"]) + "\n")


def run(path, min_chars=3600, max_chars=4400, rounds=1, resume=False, plan=None):
    """plan이 있으면 기획 파일(pipeline.py)의 진행자·첫 질문·성격·분량·제목 규칙을 쓴다. 없으면 SHOWS(초기 5편)"""
    name = Path(path).stem
    if plan:
        show = (plan["host_a"], plan["host_b"], plan["hook"], plan["concept"])
        min_chars, max_chars = plan.get("length", [min_chars, max_chars])
    else:
        show = SHOWS[name[0]]
        if name[0] == "2":  # 전문 강의형은 설명할 원리가 많아 조금 길게
            min_chars, max_chars = 4400, 5200
    src = parse_source(path)
    cfg = tomllib.loads((REPO / "ai" / "script_agent" / "config.toml").read_text())["models"]
    k = keys()
    writer = APIProvider(cfg["generator"], k, 600)
    checker = APIProvider(cfg["reviewer"], k, 600)
    fmt = dict(a=show[0], b=show[1], hook=show[2], concept=show[3], min_chars=min_chars, max_chars=max_chars)
    used = prefs_for(name, "script")
    WRITE_P, REVISE_P = with_prefs(WRITE.format(**fmt), used), with_prefs(REVISE.format(**fmt), used)
    base = {"episode_idea": src["title"], "sources": src["sources"], "facts": src["facts"], "cautions": src["cautions"]}
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    usage = []

    def call(p, system, payload, schema, tag):
        t = time.time()
        c = p.complete(system, payload, schema)
        usage.append({"step": tag, "model": c.response_model, "usage": c.usage, "sec": round(time.time() - t, 1)})
        (folder / f"{len(usage):02d}-{tag}.json").write_text(json.dumps(c.value.model_dump(), ensure_ascii=False, indent=1))
        return c.value

    prev = folder / "script.json"
    if resume and prev.exists():  # 앞서 만든 대본과 남은 지적에서 이어서 고친다
        j = json.loads(prev.read_text())
        final, check = Script.model_validate(j["script"]), Check.model_validate(j["check"])
        usage += json.loads((folder / "usage.json").read_text()) if (folder / "usage.json").exists() else []
    else:
        final = call(writer, WRITE_P, base, Script, "write")
        check = call(checker, CHECK, {**base, "lines": [x.model_dump() for x in final.lines]}, Check, "check")
    for _ in range(rounds):
        if not check.issues:
            break
        final = call(writer, REVISE_P, {**base, "draft": final.model_dump(), "issues": [i.model_dump() for i in check.issues]}, Script, "revise")
        check = call(checker, CHECK, {**base, "lines": [x.model_dump() for x in final.lines]}, Check, "recheck")
    if plan and plan.get("title"):  # 사람이 정한 제목은 모델이 바꾸지 않는다
        final.title, options = plan["title"], []
    else:
        final.title, options = title_for(name, show, final, (plan or {}).get("title_style"))
    used = used + prefs_for(name, "title")
    (folder / "script.json").write_text(json.dumps({"show": show, "title_options": options, "prefs_used": used, "script": final.model_dump(), "check": check.model_dump()}, ensure_ascii=False, indent=1))
    (folder / "script.md").write_text(to_md(show, src, final, check), encoding="utf-8")
    (folder / "usage.json").write_text(json.dumps(usage, ensure_ascii=False, indent=1))
    return {"name": name, "title": final.title, "lines": len(final.lines), "chars": chars(final), "issues_left": len(check.issues)}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    more = "--more" in sys.argv  # 앞서 만든 대본을 두 바퀴 더 고친다
    for p in args:
        try:
            print(json.dumps(run(p, rounds=2 if more else 1, resume=more), ensure_ascii=False), flush=True)
        except ProviderError as e:
            print(json.dumps({"name": Path(p).stem, "error": e.code}, ensure_ascii=False), flush=True)
