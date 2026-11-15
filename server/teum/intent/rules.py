"""음성 의도 해석 (S3-04). 규칙 기반 기준선.

입력 = STT가 돌려준 문장 (Pleos STT는 글자만 주고 명령 해석은 안 함)
출력 = {"intent", "query", "scope", "requires_disambiguation"}

v2: v1 검증에서 단어 일부만 맞아도 걸리는 문제가 나왔다("정지선"→pause, "좀비"→"비", "충전에"→이력 검색).
- 짧은 명령(다음, 멈춰 등)은 문장 전체가 명령일 때만 인정한다
- '틀어/들려' 같은 재생 동사가 있으면 재생 요청이 우선이다
- 지우는 단어와 이력 단어는 앞뒤가 한글이 아닐 때(독립된 단어일 때)만 본다
- 조사 떼기는 '고양이'→'고양' 같은 오류를 내서 하지 않는다. 검색이 조사를 견딘다
"""
import re

H, T = r"(?<![가-힣])", r"(?![가-힣])"  # 앞/뒤가 한글이 아님 = 독립된 단어


def word(w):
    return H + w + T


END = r"\s*(줘|줄래|주세요|주라)?\s*[.!?]*\s*$"
SKIP_FULL = re.compile(r"^\s*(그냥\s*)?(다음|넘겨|건너뛰어|스킵)(\s*(거|것|꺼|곡|이야기))?(\s*(틀어|들려|재생해|넘겨))?" + END)
PAUSE_FULL = re.compile(r"^\s*(잠깐\s*)?(멈춰|정지|일시\s*정지|그만|그만\s*(틀어|들을래))(\s*해)?" + END)
LIKE_FULL = re.compile(r"^\s*(이거\s*|이\s*이야기\s*)?(좋아요|좋았어|마음에\s*들어)" + END)
RESUME = [word("이어서"), word("마저"), r"계속\s*(들려|틀어|재생)", r"듣던\s*거"]
PLAY = [r"틀어", r"들려", r"재생", r"듣고\s*싶", r"찾아"]
HISTORY_WORDS = ["저번에", "지난번에", "지난번", "아까", "전에", "예전에", "어제", "들었던", "듣던", "들은"]
HISTORY = [word(w) for w in HISTORY_WORDS] + [r"듣다\s*만"]
STRIP_WORDS = HISTORY_WORDS + ["이어서", "마저", "계속", "다시", "처음부터", "좀", "한번", "한 번", "듣다 만"]
STRIP_TAIL = r"\s*((틀어|들려|재생해|찾아)\s*(줘|줄래|주세요|주라)?|듣고\s*싶(어|은데|다)?|해\s*줘)\s*[.!?]*\s*$"


def parse(text):
    t = text.strip()
    rules = []
    if SKIP_FULL.match(t):
        intent, rules = "skip", ["SKIP_FULL"]
    elif PAUSE_FULL.match(t):
        intent, rules = "pause", ["PAUSE_FULL"]
    elif LIKE_FULL.match(t):
        intent, rules = "like", ["LIKE_FULL"]
    elif any(re.search(p, t) for p in RESUME):
        intent, rules = "resume_content", [p for p in RESUME if re.search(p, t)]
    elif any(re.search(p, t) for p in PLAY):
        intent, rules = "play_content", [p for p in PLAY if re.search(p, t)]
    else:
        intent = "unknown"
    scope = "listening_history" if intent == "resume_content" or any(re.search(p, t) for p in HISTORY) else "catalog"
    q = None
    if intent in ("play_content", "resume_content"):
        q = re.sub(STRIP_TAIL, " ", t)
        for w in STRIP_WORDS:
            q = re.sub(word(re.escape(w)), " ", q)
        q = re.sub(r"\s+", " ", q).strip(" .,?!") or None
    return {"intent": intent, "query": q, "scope": scope, "requires_disambiguation": False, "matched_rules": rules}
