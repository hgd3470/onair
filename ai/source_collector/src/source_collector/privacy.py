"""Deterministic, fail-closed checks for identifiable contact/account data.

Findings contain rule names only; never echo the matched private value.
This is a format detector, not a complete natural-language PII classifier.
"""
from dataclasses import asdict
import html
import re
import unicodedata
from urllib.parse import unquote

STORY_CATEGORIES = frozenset({"C01", "C02", "C03", "C04", "C05"})
ANONYMOUS_AUTHOR = "익명"
PRIVACY_PROMPT = """
[개인정보 금지 — 품질 점수보다 우선]
모든 필드에서 SNS 아이디, 계정명, 닉네임, 프로필 링크, 이메일, 전화번호,
주민등록번호, 개인 주소 및 사연 속 일반인의 실명을 쓰지 마세요.
인물은 나/친구/동료 등 역할로 표현하세요. 사연형 author는 반드시 '익명'입니다.
계정명을 추측하거나 새로 만들지 마세요. 심사평에도 식별정보를 재인용하지 마세요.
지식 자료의 공적 인물·기관명과 출처 확인용 게시물 URL은 유지할 수 있습니다.
"""

RULES = {
    "account_handle": re.compile(r"@[\w][\w.\-]*", re.UNICODE),
    "email": re.compile(r"[\w.+-]+\s*@\s*[\w.-]+\.[a-z]{2,}", re.I),
    "account_label": re.compile(
        r"(?:계정명|계정\s*(?:아이디|이름)|인스타(?:그램)?\s*(?:아이디|id|계정)|"
        r"(?:인스타(?:그램)?|instagram)\s*[:=]\s*|"
        r"sns\s*(?:아이디|id|계정)|닉네임|사용자명|username|user\s*id|handle)"
        r"\s*(?:은|는|이|가|:|=|：)?\s*[\w@]", re.I),
    "phone": re.compile(r"(?<!\d)(?:0(?:1[016789]|2|[3-6]\d)[ .()-]*\d{3,4}[ .()-]*\d{4}|"
                        r"\+82[ .()-]*(?:0?1[016789]|2|[3-6]\d)[ .()-]*\d{3,4}[ .()-]*\d{4})(?!\d)"),
    "resident_number": re.compile(r"(?<!\d)\d{6}\s*-\s*[1-8]\d{6}(?!\d)"),
    # Instagram post/reel shortcodes contain no account name. Everything else is blocked.
    "social_profile": re.compile(
        r"(?:https?://)?(?:www\.|m\.)?(?:"
        r"instagram\.com/(?!p/[a-z0-9_-]+/?(?:[\s<>\])}|,]|$)|reel/[a-z0-9_-]+/?(?:[\s<>\])}|,]|$))[^\s<>]+|"
        r"(?:threads\.(?:net|com)|twitter\.com|x\.com|facebook\.com|tiktok\.com|"
        r"t\.me|open\.kakao\.com)/[^\s<>]+)", re.I),
    "identity_query": re.compile(r"[?&](?:igsh|igshid|username|user_id|screen_name|email|phone)=", re.I),
}


def normalize(value: str) -> str:
    for _ in range(3):
        value = html.unescape(unquote(value))
    value = unicodedata.normalize("NFKC", value)
    return "".join(c for c in value if unicodedata.category(c) != "Cf")


def privacy_codes(value) -> list[str]:
    """Inspect every string including nested metadata and keys, without exposing it."""
    codes = set()
    if isinstance(value, str):
        text = normalize(value)
        codes.update(name for name, pattern in RULES.items() if pattern.search(text))
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and normalize(key).lower() in {"username", "handle", "nickname", "account", "계정명", "닉네임"} and item:
                codes.add("account_field")
            codes.update(privacy_codes(key))
            codes.update(privacy_codes(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            codes.update(privacy_codes(item))
    return sorted(codes)


class PrivacyViolation(ValueError):
    def __init__(self, codes):
        self.codes = sorted(set(codes))
        super().__init__("privacy_blocked:" + ",".join(self.codes))


def require_private_data_free(value) -> None:
    codes = privacy_codes(value)
    if codes:
        raise PrivacyViolation(codes)


def require_safe_candidate(candidate) -> None:
    codes = privacy_codes(asdict(candidate))
    if candidate.category in STORY_CATEGORIES and candidate.author != ANONYMOUS_AUTHOR:
        codes.append("story_author_not_anonymous")
    if codes:
        raise PrivacyViolation(codes)
