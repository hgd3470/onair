"""Master CSV synchronization and Collection Log ledger writer."""

import csv
from pathlib import Path
from typing import Iterable, List, Optional
from .config import (
    CATEGORIES,
    CSV_FIELDNAMES,
    DEFAULT_CSV_EN,
    DEFAULT_CSV_KO,
    SCRIPT_AGENT_ROOT,
    SOURCE_COLLECTOR_ROOT,
)
from .models import CandidateSource
from .privacy import require_safe_candidate


class LedgerManager:
    """Manages appending accepted sources to CSV ledgers and generating collection-log.md."""

    def __init__(
        self,
        csv_paths: Optional[List[Path]] = None,
        source_library_root: Optional[Path] = None,
    ):
        if csv_paths is None:
            local_ko = SOURCE_COLLECTOR_ROOT / "전체_소스_목록.csv"
            local_en = SOURCE_COLLECTOR_ROOT / "all_sources_summary.csv"
            csv_paths = [DEFAULT_CSV_KO, DEFAULT_CSV_EN, local_ko, local_en]
        self.csv_paths = csv_paths
        self.source_library_root = (
            source_library_root
            if source_library_root is not None
            else SOURCE_COLLECTOR_ROOT / "output"
        )

    def append_to_csvs(self, accepted: Iterable[CandidateSource]) -> int:
        accepted = list(accepted)
        for candidate in accepted:
            require_safe_candidate(candidate)
        new_rows = [c.to_csv_row() for c in accepted]
        if not new_rows:
            return 0

        for cp in self.csv_paths:
            cp.parent.mkdir(parents=True, exist_ok=True)
            existing_rows = []
            existing_ids = set()
            if cp.exists():
                with open(cp, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        existing_rows.append(r)
                        existing_ids.add(r.get("소스ID", ""))

            # Avoid duplicating same ID if re-running
            to_add = [r for r in new_rows if r["소스ID"] not in existing_ids]
            combined = existing_rows + to_add

            with open(cp, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
                writer.writeheader()
                writer.writerows(combined)

        return len(new_rows)

    def write_collection_log(
        self,
        batch_id: str,
        accepted: List[CandidateSource],
        target_dir: Path,
        mode: str = "staging",
    ) -> Path:
        for candidate in accepted:
            require_safe_candidate(candidate)
        log_path = self.source_library_root / batch_id / "collection-log.md"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        now_str = accepted[0].collected_at if accepted else "2026-11-09T10:38:00+09:00"
        lines = [
            "# 소스 수집 원장 (Collection Log)",
            "",
            f"- **작업 ID**: `{batch_id}`",
            f"- **요청 일시**: {now_str} (KST)",
            f"- **실행 모드**: {mode} (`{target_dir}`)",
            "- **수집 가이드**: `SOURCE_COLLECTION_GUIDE.md` 준수",
            "- **수집 현황 요약**:",
            f"  - **최종 채택 (Accepted)**: {len(accepted)}건 (전량 AI 평가 9~10점 통과)",
            "  - **드랍·개인정보 차단**: run 요약의 privacy_rejections / validation_failures 참조",
            "  - **보류 (Held)**: 0건",
            "",
            f"## 1. 채택 소스 목록 ({len(accepted)}건)",
            "",
            "| Source ID | 카테고리 | 제목 | 원출처 URL | 작성자/발행처 | 라이선스 | AI 점수 | AI 심사평 | 본문 SHA-256 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]

        for c in sorted(accepted, key=lambda x: x.id):
            cat_name = CATEGORIES.get(c.category, {}).get("name", "")
            url_disp = f"[{c.url[:38]}...]({c.url})" if len(c.url) > 38 else f"[{c.url}]({c.url})"
            reason_clean = c.eval_reason.replace("\n", " ").replace("|", "/")
            sha_short = (c.sha256 or "")[:16]
            lines.append(
                f"| `{c.id}` | {c.category} ({cat_name}) | {c.title} | {url_disp} | {c.author} | {c.license_kind} | **{c.score}/10** | {reason_clean} | `{sha_short}...` |"
            )

        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return log_path
