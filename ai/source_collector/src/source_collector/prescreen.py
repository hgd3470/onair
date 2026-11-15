"""Pre-screening and deduplication engine against historical ledgers and demo benchmarks."""

from collections import defaultdict
import csv
from pathlib import Path
import re
import threading
from typing import Dict, Iterable, List, Optional, Set
from .config import DEFAULT_CSV_KO, DEMO_TXT, SOURCE_COLLECTOR_ROOT
from .models import CandidateSource, PreScreenResult
from .privacy import PrivacyViolation, require_safe_candidate


def normalize_title(title: str) -> str:
    """Normalize title for exact and near-exact duplicate matching."""
    t = title.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def normalize_tokens(title: str) -> Set[str]:
    """Extract content tokens (>=2 chars) for high-overlap semantic check."""
    cleaned = re.sub(r"[^\w\s가-힣]", " ", title.lower())
    return {w for w in cleaned.split() if len(w) >= 2}


class Deduplicator:
    """Deduplicates candidate sources against master CSVs and demo management text."""

    def __init__(
        self,
        csv_paths: Optional[List[Path]] = None,
        demo_path: Optional[Path] = None,
    ):
        if csv_paths is None:
            local_csv = SOURCE_COLLECTOR_ROOT / "전체_소스_목록.csv"
            csv_paths = [p for p in [DEFAULT_CSV_KO, local_csv] if p.exists()]
        self.csv_paths = csv_paths
        self.demo_path = demo_path if demo_path is not None else DEMO_TXT

        self.existing_ids: Set[str] = set()
        self.existing_titles: Set[str] = set()
        self.existing_norm_titles: Set[str] = set()
        self.existing_urls: Set[str] = set()
        self.demo_titles: Set[str] = set()
        self.demo_norm_titles: Set[str] = set()
        self.category_titles: Dict[str, Set[str]] = defaultdict(set)
        self._lock = threading.Lock()

        self._load_all()

    def _load_all(self) -> None:
        # 1. Load Master CSVs
        for cp in self.csv_paths:
            if not cp.exists():
                continue
            try:
                with open(cp, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        sid = (row.get("소스ID") or "").strip()
                        cat = (row.get("카테고리_코드") or "").strip()
                        title = (row.get("주제_제목") or "").strip()
                        url = (row.get("원본_URL") or "").strip()
                        if sid:
                            self.existing_ids.add(sid)
                        if title:
                            self.existing_titles.add(title)
                            self.existing_norm_titles.add(normalize_title(title))
                            if cat:
                                self.category_titles[cat].add(title)
                        if url:
                            self.existing_urls.add(url)
            except Exception:
                pass

        # 2. Load Demo Management file
        if self.demo_path and self.demo_path.exists():
            try:
                text = self.demo_path.read_text(encoding="utf-8")
                patterns = [
                    r"\[\d+\]\s+[A-Z]\d{2}\s*\|\s*(.+)",
                    r"^###\s+[A-Z]\d{2}\.\s*(.+)",
                    r"\[([^\]]+)\]\(https://www\.instagram\.com/p/[^)]+\)",
                    r"주제:\s*(.+)",
                ]
                for pat in patterns:
                    for m in re.finditer(pat, text, re.MULTILINE):
                        dt = m.group(1).strip()
                        if dt and dt != "추가 릴스" and "비교본" not in dt:
                            self.demo_titles.add(dt)
                            self.demo_norm_titles.add(normalize_title(dt))
                for m in re.finditer(r"https://www\.instagram\.com/p/[A-Za-z0-9_-]+/?", text):
                    self.existing_urls.add(m.group(0).rstrip("/") + "/")
            except Exception:
                pass

    def check_title(self, title: str) -> PreScreenResult:
        norm = normalize_title(title)
        if not norm:
            return PreScreenResult(False, "empty_title", None, "Title is empty")
        if norm in self.existing_norm_titles:
            return PreScreenResult(False, "csv_title_duplicate", title, f"Title already exists in master CSV: {title}")
        if norm in self.demo_norm_titles:
            return PreScreenResult(False, "demo_title_duplicate", title, f"Title already exists in demo file: {title}")
        return PreScreenResult(True)

    def check_url(self, url: str, category: str) -> PreScreenResult:
        u = url.strip()
        if not u:
            return PreScreenResult(False, "empty_url", None, "URL is empty")
        # For C01~C05, Instagram URL is mandatory and must be unique
        if category in {"C01", "C02", "C03", "C04", "C05"}:
            if not re.match(r"^https://www\.instagram\.com/p/[A-Za-z0-9_-]+/?$", u):
                return PreScreenResult(
                    False,
                    "invalid_instagram_url",
                    u,
                    f"C01~C05 requires valid Instagram post URL (https://www.instagram.com/p/...), got: {u}"
                )
            u_norm = u.rstrip("/") + "/"
            if u_norm in self.existing_urls or u in self.existing_urls:
                return PreScreenResult(
                    False,
                    "instagram_url_duplicate",
                    u,
                    f"Instagram URL already exists in ledger: {u}"
                )
        return PreScreenResult(True)

    def check_candidate(self, candidate: CandidateSource) -> PreScreenResult:
        try:
            require_safe_candidate(candidate)
        except PrivacyViolation as exc:
            return PreScreenResult(False, "privacy_blocked", None, str(exc))
        with self._lock:
            if candidate.id in self.existing_ids:
                return PreScreenResult(False, "id_duplicate", candidate.id, f"Source ID already exists: {candidate.id}")

            title_res = self.check_title(candidate.title)
            if not title_res.is_valid:
                return title_res

            if candidate.ai_generated:
                if (candidate.category not in {"C01", "C02", "C03", "C04", "C05"}
                        or candidate.url or candidate.attribution or candidate.source_id_ref
                        or candidate.license_kind != "팀 자체 AI 창작물"):
                    return PreScreenResult(False, "invalid_ai_creation_provenance")
                return PreScreenResult(True, reason="AI fiction: no external source URL")
            url_res = self.check_url(candidate.url, candidate.category)
            if not url_res.is_valid:
                return url_res

            return PreScreenResult(True, reason="Passed all pre-screening deduplication checks")

    def register(self, candidate: CandidateSource) -> None:
        """Register candidate into in-memory sets to prevent intra-batch collision."""
        with self._lock:
            self.existing_ids.add(candidate.id)
            self.existing_titles.add(candidate.title)
            self.existing_norm_titles.add(normalize_title(candidate.title))
            if candidate.category:
                self.category_titles[candidate.category].add(candidate.title)
            if candidate.url:
                self.existing_urls.add(candidate.url)
                self.existing_urls.add(candidate.url.rstrip("/") + "/")

    def stats(self) -> dict:
        return {
            "csv_ids": len(self.existing_ids),
            "csv_titles": len(self.existing_titles),
            "demo_titles": len(self.demo_titles),
            "tracked_urls": len(self.existing_urls),
        }
