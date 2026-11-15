"""합성 음원 받아쓰기 대조 (검수 뒤 자동화). teum_admin.run_jobs가 합성이 끝난 편을 모아 한 번 부른다.

입력(stdin): [{"id", "mp3", "text"}, ...]  출력(stdout): 편마다 JSON 한 줄 {"id", "cer", "diffs": [[대본, 받아쓰기], ...], "hyp": 받아쓰기 원문}
받아쓰기의 숫자·단위(cm, km)·대문자 약어(MET)는 대본처럼 한글 읽기로 바꿔 대조한다. 숫자는 대본 그 자리의 읽기(한자어·고유어)를 따른다.
Whisper large-v3-turbo, GPU 0만. CER은 한글·숫자·영문만 남기고 띄어쓰기를 뺀 글자 기준.
기준 3%: 11/5 37편 자동 점검(CER 중앙값 0.01)에서 3%를 넘은 편이 5편이었다.
"""
import difflib
import json
import os
import re
import sys

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
import torch  # noqa: E402
from transformers import pipeline  # noqa: E402

KEEP = re.compile(r"[^0-9A-Za-z가-힣\s]")
# Whisper가 조용한 끝부분에 지어내는 문구(유튜브 자막 학습 흔적)
HALLU = re.compile(r"(자막 ?제공( ?및)?|광고를 포함하고 있습니다|시청해 ?주셔서 감사합니다|구독과 좋아요[^.]{0,20})")
DIG = "영일이삼사오육칠팔구"


def sino(n):
    """정수 → 한자어 읽기 (1707 → 천칠백칠). 대본은 숫자를 한글로 풀어 쓰고 Whisper는 아라비아 숫자로 적어서 맞춘다"""
    if n == 0:
        return "영"
    out = ""
    for big, unit in ((10**8, "억"), (10**4, "만"), (1, "")):
        q, n = divmod(n, big)
        if not q:
            continue
        s = ""
        for d, u in ((1000, "천"), (100, "백"), (10, "십"), (1, "")):
            k, q = divmod(q, d)
            if k:
                s += ("" if (k == 1 and u) else DIG[k]) + u
        out += s + unit
    return out


NAT1 = ["", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉"]
NAT1S = ["", "하나", "둘", "셋", "넷", "다섯", "여섯", "일곱", "여덟", "아홉"]
NAT10 = ["", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔"]
UNITS = [("km", "킬로미터"), ("cm", "센티미터"), ("mm", "밀리미터"), ("kg", "킬로그램"), ("mg", "밀리그램"), ("ml", "밀리리터"),
         ("kcal", "킬로칼로리"), ("℃", "도"), ("°C", "도"), ("%", "퍼센트"), ("m", "미터"), ("g", "그램"), ("L", "리터"), ("l", "리터")]
UNIT_RE = re.compile(r"(?<=\d)\s*(" + "|".join(re.escape(u) for u, _ in sorted(UNITS, key=lambda x: -len(x[0]))) + r")(?![A-Za-z])")
ALPHA = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "에이 비 씨 디 이 에프 지 에이치 아이 제이 케이 엘 엠 엔 오 피 큐 알 에스 티 유 브이 더블유 엑스 와이 제트".split()))


def native(n):
    """고유어 읽기 후보 (6 → 여섯, 18 → 열여덟, 2 → 두·둘, 20 → 스무·스물). 1~99만"""
    if not 0 < n < 100:
        return []
    t, o = divmod(n, 10)
    out = [NAT10[t] + NAT1[o], NAT10[t] + NAT1S[o]]
    if n == 20:
        out.insert(0, "스무")
    return list(dict.fromkeys(out))


def spell(s):
    """대문자 약어를 글자 이름으로 (MET → 엠이티). 대본은 한글로 풀어 쓴다"""
    return re.sub(r"[A-Z]+(?![a-z])", lambda m: "".join(ALPHA[c] for c in m[0]), s)


def normalize_hyp(s, ref=""):
    """Whisper 표기를 대본 표기(숫자·단위·약어를 한글로 풀어 씀)에 맞춘다.
    숫자는 한자어 읽기가 기본이고, 대본 그 자리에 고유어 읽기(여섯 개, 열여덟 살)가 있으면 고유어로 맞춘다.
    Whisper는 두 읽기를 모두 아라비아 숫자로 적어서, 한자어·고유어를 바꿔 읽은 오류는 이 대조로 잡지 못한다."""
    s = HALLU.sub(" ", s).replace(",", "")
    s = UNIT_RE.sub(lambda m: dict(UNITS)[m[1]], s)
    s = spell(s)
    refc = "".join(words(ref))
    parts = re.split(r"(\d+(?:\.\d+)?)", s)
    out = ""
    for i, seg in enumerate(parts):
        if i % 2 == 0:
            out += seg
            continue
        whole, _, frac = seg.partition(".")
        if len(whole) >= 13:
            out += seg
            continue
        cands = [sino(int(whole))] + ([] if frac else native(int(whole)))
        nxt = "".join(words(parts[i + 1]))[:2] if i + 1 < len(parts) else ""
        prv = "".join(words(out))[-2:]
        best = cands[0]
        for a, b in ((prv, nxt), ("", nxt), (prv, nxt[:1]), ("", nxt[:1]), (prv, "")):
            hit = [c for c in cands if (a or b) and (a + c + b) in refc]
            if hit:
                best = hit[0]
                break
        out += best + ("점" + "".join(DIG[int(d)] for d in frac) if frac else "")
    return out


def words(s):
    return KEEP.sub(" ", s).split()


def cer(ref, hyp):
    a, b = "".join(words(ref)), "".join(words(hyp))
    if not a:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return round(prev[-1] / len(a), 3)


def diffs(ref, hyp, n=5):
    r, h = words(ref), words(hyp)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=r, b=h, autojunk=False).get_opcodes():
        if op != "equal":
            a, b = " ".join(r[i1:i2]), " ".join(h[j1:j2])
            if a.replace(" ", "") != b.replace(" ", ""):  # 띄어쓰기만 다른 것은 뺀다
                out.append([a, b])
    return out[:n]


def main():
    items = json.load(sys.stdin)
    asr = pipeline("automatic-speech-recognition", model="openai/whisper-large-v3-turbo",
                   torch_dtype=torch.float16, device="cuda:0")
    for it in items:
        try:
            raw = asr(it["mp3"], generate_kwargs={"language": "ko", "task": "transcribe"}, return_timestamps=True)["text"]
            hyp = normalize_hyp(raw, it["text"])
            print(json.dumps({"id": it["id"], "cer": cer(spell(it["text"]), hyp), "diffs": diffs(spell(it["text"]), hyp), "hyp": raw.strip()}, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"id": it["id"], "error": str(e)[:200]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
