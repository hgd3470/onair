"""여러 모듈이 같이 쓰는 글자 처리와 분류 이름."""
import re

from teum.catalog.categories import CODES

CATEGORY_KO = {"funny": "재미있는 썰", "insight": "인사이트", "emotional": "감동", "motivation": "동기부여", "science": "과학", "history": "역사",
               "economy": "시사·경제", "life": "생활", "ad": "광고"}

CATEGORY_KO.update(CODES)


def normalize_for_match(text):
    """비교용 정규화: 한글·영문·숫자만 남기고 공백과 문장부호를 지운다."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")
