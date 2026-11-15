"""Data models for candidate sources and evaluation results."""

from dataclasses import dataclass
from typing import Any, Dict, Optional
from .config import CATEGORIES


@dataclass
class CandidateSource:
    id: str
    category: str
    title: str
    url: str
    author: str
    source_id_ref: str
    source_name: str
    production_type: str
    license_kind: str
    license_info: Dict[str, Any]
    attribution: str
    summary: str
    text: str
    score: int = 0
    eval_reason: str = ""
    collected_at: str = ""
    sha256: str = ""
    batch_id: str = ""
    ai_generated: bool = False

    def to_csv_row(self) -> Dict[str, Any]:
        """Convert to 15-column master CSV row."""
        cat_name = CATEGORIES.get(self.category, {}).get("name", "")
        return {
            "소스ID": self.id,
            "카테고리_코드": self.category,
            "카테고리명": cat_name,
            "주제_제목": self.title,
            "원천목록_ID": self.source_id_ref,
            "원천_명칭": self.source_name,
            "작성자_발행처": self.author,
            "원본_URL": self.url,
            "간략_주제_요약": self.summary,
            "제작유형": self.production_type,
            "라이선스": self.license_kind,
            "본문_글자수": len(self.text),
            "수집일시": self.collected_at,
            "AI_평가점수": self.score,
            "AI_심사평": self.eval_reason
        }


@dataclass
class PreScreenResult:
    is_valid: bool
    duplicate_type: Optional[str] = None
    matched_entry: Optional[str] = None
    reason: str = ""
