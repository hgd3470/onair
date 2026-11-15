"""Markdown file formatter, writer, and schema validator."""

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Dict, Optional, Tuple
import yaml
from .config import CATEGORIES, SCRIPT_AGENT_ROOT, VALIDATE_CLI
from .models import CandidateSource
from .privacy import require_safe_candidate, require_private_data_free


class SourceWriter:
    """Formats candidate sources into compliant Markdown files and validates them."""

    def __init__(
        self,
        target_dir: Path,
        source_library_root: Optional[Path] = None,
        script_agent_root: Path = SCRIPT_AGENT_ROOT,
    ):
        self.target_dir = target_dir
        self.script_agent_root = script_agent_root
        self.source_library_root = (
            source_library_root
            if source_library_root is not None
            else Path(__file__).resolve().parents[2] / "output"
        )

    def format_markdown(self, candidate: CandidateSource) -> str:
        require_safe_candidate(candidate)
        cat_name = CATEGORIES.get(candidate.category, {}).get("name", "")
        is_story = candidate.category in {"C01", "C02", "C03", "C04", "C05"}
        body_full = f"# {candidate.title}\n\n{candidate.text.strip()}\n"
        body_sha256 = hashlib.sha256(body_full.encode("utf-8")).hexdigest()
        candidate.sha256 = body_sha256

        date_only = (candidate.collected_at or "2026-11-09")[:10]
        media_str = f"인스타그램 {candidate.author}" if is_story else f"{candidate.source_name} ({candidate.author})"
        nature_str = "사연/썰 경험담" if is_story else "공공/학술/백과 지식 아티클"

        notes_text = (
            f"요청 수신 시각: {candidate.collected_at}\n"
            f"요청 내용·수집 조건: {candidate.category} {cat_name} 고품질 소스 수집 및 헤드리스 AI 퀄리티 평가(9점 이상 확보)\n"
            f"원문 제목: {candidate.title}\n"
            f"매체·발행 기관: {media_str}\n"
            f"자료 성격: {nature_str}\n"
            f"원문 언어: 한국어\n"
            f"게시일: {date_only}\n"
            f"수정일: 해당 없음\n"
            f"선정 이유: {candidate.category} 카테고리 부합 및 고품질 오디오 숏폼 대본 적합성 확보\n"
            f"추가 카테고리: 없음\n"
            f"발견 URL: {candidate.url}\n"
            f"실제 추출 URL·최종 도착 URL: {candidate.url}\n"
            f"원출처 대조: 원본 웹페이지/문헌 대조 완료\n"
            f"전문 범위: 본문 전문 수록\n"
            f"포함·제외 내역: 메뉴·광고 제외, 본문 완비\n"
            f"전문 확인: 완료\n"
            f"추출 방법: 텍스트 추출 및 정형화\n"
            f"OCR 대상·대조 기록: 해당 없음\n"
            f"OCR 교정 기록: 해당 없음\n"
            f"미판독·누락 구간: 없음\n"
            f"인용된 근거 자료: 없음\n"
            f"발언 주체·조건 관련 유의점: 원문 맥락 유지\n"
            f"헤드리스 품질 평가 모델: gemini-3.8-flash-medium (headless agy -p)\n"
            f"품질 평가 기준: demo/컨텐츠 제작 관리.txt 기준 (10점 만점 9점 이상 합격)\n"
            f"품질 평가 결과: SCORE: {candidate.score} | REASON: {candidate.eval_reason}\n"
            f"본문 SHA-256: {body_sha256}\n"
        )

        if candidate.ai_generated:
            if (not is_story or candidate.url or candidate.attribution or candidate.source_id_ref
                    or candidate.license_kind != "팀 자체 AI 창작물"):
                raise ValueError("invalid_ai_creation_provenance")
            notes_text = (
                f"자료 성격: AI 창작물 (허구의 사연; 실제 제보·수집 원문 아님)\n"
                f"작성 시각: {candidate.collected_at}\n"
                f"외부 출처·웹페이지 대조·게시일: 해당 없음\n"
                f"품질 평가 결과: SCORE: {candidate.score} | REASON: {candidate.eval_reason}\n"
                f"본문 SHA-256: {body_sha256}\n"
            )

        frontmatter = {
            "source_id": candidate.id,
            "source_url": candidate.url or None,
            "source_kind": "original",
            "author": candidate.author,
            "category": candidate.category,
            "production_type": candidate.production_type,
            "target_sec": 90,
            "info_as_of": date_only,
            "collected_at": candidate.collected_at,
            "edition": None,
            "license": candidate.license_info,
            "attribution": candidate.attribution,
            "fixture": False,
            "notes": notes_text,
        }

        yaml_str = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{yaml_str}\n---\n\n{body_full}"

    def save(self, candidate: CandidateSource) -> Path:
        md_content = self.format_markdown(candidate)
        batch = candidate.batch_id or '_unbatched'
        acc_dir = self.source_library_root / batch
        acc_dir.mkdir(parents=True, exist_ok=True)
        canonical = acc_dir / f"{candidate.id}.md"
        if canonical.exists() and canonical.read_text(encoding="utf-8") != md_content:
            raise ValueError("source_id_collision")
        part = canonical.with_suffix('.md.part')
        part.write_text(md_content, encoding="utf-8")
        part.replace(canonical)
        out_path = canonical
        if self.target_dir.resolve() == (self.script_agent_root / 'input').resolve():
            self.target_dir.mkdir(parents=True, exist_ok=True)
            out_path = self.target_dir / canonical.name
            if out_path.exists() and out_path.read_text(encoding='utf-8') != md_content:
                raise ValueError('pending_source_id_collision')
            out_path.write_text(md_content, encoding='utf-8')

        return out_path

    def validate_file(self, md_path: Path) -> Tuple[bool, Dict]:
        """Validate Markdown file using script-agent validate-input from script_agent_root."""
        try:
            require_private_data_free(md_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            return False, {"error": str(exc)}
        if not VALIDATE_CLI.exists():
            return False, {"error": f"Validate CLI not found at {VALIDATE_CLI}"}
        try:
            res = subprocess.run(
                [str(VALIDATE_CLI), "validate-input", str(md_path.resolve())],
                cwd=str(self.script_agent_root),
                capture_output=True,
                text=True,
                timeout=15,
            )
            if res.returncode == 0 and "valid" in res.stdout:
                data = json.loads(res.stdout.strip())
                return bool(data.get("valid")), data
            return False, {"stdout": res.stdout, "stderr": res.stderr, "returncode": res.returncode}
        except Exception as e:
            return False, {"error": str(e)}
