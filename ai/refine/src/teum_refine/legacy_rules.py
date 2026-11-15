"""정제(S2-06) 규칙 검사 뼈대 — 대본 1편을 수집 기록·추출 결과와 대조해 판정한다.

사용: python3 tools/refine_check.py <유형 폴더> [대본 파일명]
  유형 폴더 안에 1_source.json, 2_extract.json, 3_script*.md 가 있어야 한다.
출력: 같은 폴더에 4_refine[_<대본이름>].json, 화면에 요약.

LLM 단계(유해성 세부 판단, 대본 품질 점수)는 자리만 두고 여기서는 규칙 검사만 한다.
"""
import json
import re
import sys
from pathlib import Path

HARM_WORDS = ["씨발", "병신", "좆", "새끼", "자살", "살해 방법"]  # 예시. 실제 목록은 S2-04 기준으로 채운다
ADVICE_WORDS = ["매수하세요", "매도하세요", "사세요", "파세요", "추천 종목", "목표가", "지금 사", "담으세요"]

# 유형별 검사 강도: 어떤 검사를 막는 조건(block)으로, 어떤 검사를 경고(warn)로 볼지
POLICY = {
    "economy": {"numbers": "block", "similarity": "warn", "must_not_use": "block", "advice": "block", "harm": "block"},
    "life": {"numbers": "block", "similarity": "block", "must_not_use": "block", "advice": "warn", "harm": "block"},
    "story": {"numbers": "warn", "similarity": "block", "must_not_use": "block", "advice": "warn", "harm": "block"},
}
SENT_CONTAIN_LIMIT = 0.5  # 대본 한 문장의 글자 4-gram 중 원문에 있는 비율
LCS_LIMIT = 20  # 원문과 그대로 겹치는 가장 긴 구간(공백 제외 글자 수)


def read_body(md: str) -> str:
    m = re.search(r"## 2\. 낭독 본문\s*```text\n(.*?)```", md, re.S)
    return m.group(1).strip() if m else ""


def norm(s: str) -> str:
    return re.sub(r"[\s\.,·'\"“”‘’!?()~\-]", "", s)


def grams(s: str, n: int = 4) -> set:
    s = norm(s)
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def lcs_len(a: str, b: str) -> int:
    a, b = norm(a), norm(b)
    best, prev = 0, [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


UNIT = {"천": 1_000, "만": 10_000, "억": 100_000_000}


def numbers_in(text: str) -> list:
    """숫자를 뽑는다. '5천만'·'250만'처럼 한국어 단위어가 붙으면 곱해서 하나의 값으로 본다."""
    out = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)([천만억]*)", text):
        v = float(m.group(1).replace(",", ""))
        for u in m.group(2):
            v *= UNIT[u]
        out.append(v)
    return out


def allowed_numbers(extract: dict) -> set:
    vals = set()
    for f in extract.get("facts", []):
        for k in ("value", "prev", "change", "change_pct"):
            if isinstance(f.get(k), (int, float)):
                vals.add(abs(float(f[k])))
        for v in f.get("numbers", []):
            vals.add(float(v))
        for v in numbers_in(f.get("text", "") + " " + f.get("label", "")):
            vals.add(v)
    return vals


def check(folder: Path, script_name: str) -> dict:
    sources = json.loads((folder / "1_source.json").read_text(encoding="utf-8"))
    extract = json.loads((folder / "2_extract.json").read_text(encoding="utf-8"))
    body = read_body((folder / script_name).read_text(encoding="utf-8"))
    kind = extract["script_type"]
    policy = POLICY[kind]
    checks = []

    # 1. 라이선스 규칙: 원천별 이용 조건
    lic_notes, lic_result = [], "pass"
    for s in sources:
        lic = s["license"]
        if lic["commercial"] == "no":
            lic_result = "block"
            lic_notes.append(f"{s['source_id']}: 상업 이용 불가")
        elif lic["commercial"] == "non-commercial-only":
            lic_result = "warn" if lic_result == "pass" else lic_result
            lic_notes.append(f"{s['source_id']}: 비상업(데모·시범)만, 상용 전 승인 필요")
    checks.append({"name": "license", "result": lic_result, "detail": lic_notes})

    # 2. 숫자 대조: 대본 숫자가 추출된 사실에 있는가
    allowed = allowed_numbers(extract)
    date_like = {float(d) for d in re.findall(r"(\d+)월|(\d+)일", body) for d in d if d}
    bad = [n for n in numbers_in(body) if n not in allowed and n not in date_like]
    res = "pass" if not bad else policy["numbers"]
    checks.append({"name": "numbers", "result": res, "detail": {"not_in_facts": bad}})

    # 3. 원문 유사도: 문장별 4-gram 포함률 + 최장 공통 구간
    src_text = ""
    for s in sources:
        if s.get("text_path"):
            src_text += (folder.parent / s["text_path"]).read_text(encoding="utf-8") + "\n"
    sim = {"max_sentence_containment": 0.0, "max_lcs": 0, "flagged": []}
    if src_text:
        src_grams = grams(src_text)
        for sent in [x for x in re.split(r"(?<=[.?!요다])\s+|\n", body) if len(norm(x)) >= 8]:
            g = grams(sent)
            c = len(g & src_grams) / len(g) if g else 0.0
            l = lcs_len(sent, src_text)
            sim["max_sentence_containment"] = max(sim["max_sentence_containment"], round(c, 2))
            sim["max_lcs"] = max(sim["max_lcs"], l)
            if c >= SENT_CONTAIN_LIMIT or l >= LCS_LIMIT:
                sim["flagged"].append({"sentence": sent, "containment": round(c, 2), "lcs": l})
    res = "pass" if not sim["flagged"] else policy["similarity"]
    if not src_text:
        res, sim["note"] = "pass", "원문 텍스트 없음(수치 원천) — 숫자 대조로 대신함"
    checks.append({"name": "similarity", "result": res, "detail": sim})

    # 4. 원천 고유 요소 금지어(인물·장소·사건번호·고유 수치)
    hits = [w for w in extract.get("must_not_use", []) if w in body]
    checks.append({"name": "must_not_use", "result": "pass" if not hits else policy["must_not_use"], "detail": hits})

    # 5. 투자 권유 표현
    hits = [w for w in ADVICE_WORDS if w in body]
    checks.append({"name": "advice", "result": "pass" if not hits else policy["advice"], "detail": hits})

    # 6. 유해 표현(규칙). 세부 판단은 LLM 단계 자리
    hits = [w for w in HARM_WORDS if w in body]
    checks.append({"name": "harm_rule", "result": "pass" if not hits else policy["harm"], "detail": hits})

    # 7. 출처 문구가 본문 끝 안내에 들어갔는가(정보형만)
    attr = extract.get("attribution", "")
    key = re.split(r"[ (]", attr)[0] if attr else ""
    if kind in ("economy", "life"):
        ok = bool(key) and key in body
        checks.append({"name": "attribution_in_body", "result": "pass" if ok else "warn", "detail": key})

    # 8. LLM 단계(자리만): 유해성 세부·사실 왜곡·대본 품질
    checks.append({"name": "llm_review", "result": "todo", "detail": "정제 LLM(생성 모델과 다른 모델, 3090)으로 유해성·왜곡·품질 점수"})

    results = [c["result"] for c in checks]
    decision = "reject" if "block" in results else ("pass_with_flags" if "warn" in results else "pass")
    return {"script": script_name, "script_type": kind, "source_ids": extract["source_ids"], "decision": decision,
            "checks": checks}


def main():
    folder = Path(sys.argv[1])
    names = [sys.argv[2]] if len(sys.argv) > 2 else sorted(p.name for p in folder.glob("3_script*.md"))
    for name in names:
        out = check(folder, name)
        suffix = "" if name == "3_script.md" else "_" + Path(name).stem.replace("3_script_", "")
        (folder / f"4_refine{suffix}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[{folder.name}/{name}] {out['decision']}")
        for c in out["checks"]:
            if c["result"] not in ("pass",):
                print(f"  - {c['name']}: {c['result']} {json.dumps(c['detail'], ensure_ascii=False)[:300]}")


if __name__ == "__main__":
    main()
