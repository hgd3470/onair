"""Headless AI Quality Judge (LLM-as-a-Judge) and Re-elevation Engine."""

import re
import subprocess
from typing import Tuple
from .config import CATEGORIES
from .models import CandidateSource
from .privacy import PrivacyViolation, require_safe_candidate, require_private_data_free, PRIVACY_PROMPT


class HeadlessJudge:
    """Evaluates and elevates candidate sources via disposable headless agy sessions."""

    def __init__(
        self,
        model: str = "gemini-3.8-flash-medium",
        min_score: int = 9,
        timeout: int = 80,
        max_retries: int = 2,
        mock: bool = False,
    ):
        self.model = model
        self.min_score = min_score
        self.timeout = timeout
        self.max_retries = max_retries
        self.mock = mock

    def build_eval_prompt(self, candidate: CandidateSource) -> str:
        cat = candidate.category
        cat_name = CATEGORIES.get(cat, {}).get("name", "")
        is_story = cat in {"C01", "C02", "C03", "C04", "C05"}

        if is_story:
            benchmark_desc = (
                "- S01: 100일 기념 여행 조수석 실수 사연 (구체적 인물, 생생한 대화, 갈등의 반전과 여운)\n"
                "- S05: 권고사직 뒤 새벽에 온 문자 사연 (현실적 디테일, 감정선, 극적인 서사)"
            )
            criteria_desc = (
                "1. 형식 및 진정성: 단순 팁/정보/일반론이 아니라 실제 겪은 듯한 1인칭 사연/썰 형태이며 구체적인 등장인물, 대화(따옴표 2회 이상), 감정 변화가 생생한가?\n"
                "2. 오디오 숏폼 흡인력: 귀로 들었을 때 첫 문장부터 호기심을 유발하고, 기승전결 위기와 반전/여운이 뚜렷한가?"
            )
        else:
            benchmark_desc = (
                "- H01: 일주일에 열두 시간 - 신장 투석 이야기 (구체적 수치, 생활 밀착, 깊은 울림)\n"
                "- I02: 제임스 랜디의 초능력 검증 (명확한 사실 검증, 흥미진진한 지적 긴장감, 명쾌한 질문)\n"
                "- M04: 로저 배니스터의 4분 마일 (통념을 깬 구체적 과정과 행동 지침)"
            )
            criteria_desc = (
                "1. 형식 및 완성도: 신뢰할 수 있는 출처와 구체적 실험·사례·데이터에 기반하며, 단순 나열이 아닌 오디오로 들었을 때 귀를 기울이게 되는 명확한 장면과 서사가 있는가?\n"
                "2. 오디오 숏폼 적합성: 듣는 이의 지적 호기심을 단번에 자극하고 일상에 적용할 수 있는 통찰(Insight)이 살아있는가?"
            )

        return f"""당신은 오디오 숏폼 대본용 소스의 퀄리티를 엄격히 평가하는 전문 에디터입니다.
도구를 일체 사용하지 말고 오직 평가 결과만 한 줄로 출력하세요.

[기준 콘텐츠 (Gold Standard)]
{benchmark_desc}

[평가 대상 소스]
카테고리: {cat} ({cat_name})
제목: {candidate.title}
자료 유형: {"AI 창작물 (허구; 외부 출처 없음)" if candidate.ai_generated else "외부 원문"}
출처 URL: {candidate.url or "해당 없음"}
내용:
{candidate.text}

[평가 기준]
{criteria_desc}
3. 점수는 10점 만점 정수로 평가합니다. 기준 콘텐츠 대비 손색없는 탁월한 소스만 9점 이상 부여하세요. (9점 미만 탈락)

반드시 아래 형식으로만 한 줄로 응답하세요:
SCORE: <정수 1~10> | REASON: <한줄 심사평>"""

    def build_elevate_prompt(self, candidate: CandidateSource, rejection_reason: str) -> str:
        cat = candidate.category
        cat_name = CATEGORIES.get(cat, {}).get("name", "")
        is_story = cat in {"C01", "C02", "C03", "C04", "C05"}

        if is_story:
            rule_detail = "- 사연/썰: 1인칭 주인공 시점, 생생한 구어체 대화(큰따옴표 최소 3회 이상), 아찔한 위기 순간과 심장 뛰는 긴장감, 허를 찌르는 반전 또는 가슴 뭉클한 여운을 반드시 담을 것."
        else:
            rule_detail = "- 지식/인사이트: 구체적인 연도, 인물, 통계 수치(%, 단위), 핵심 작동 메커니즘을 밝히고, 시작부터 호기심을 자극하는 질문과 일상 적용점을 담은 명쾌한 지적 서사로 완성할 것."

        return f"""당신은 오디오 숏폼 대본용 최고급 소스 작가입니다.
아래 소스는 전문 평가에서 9점 미만을 받았습니다 (탈락 사유: {rejection_reason}).
이 소스를 기준 콘텐츠(S01, S05, H01, I02, M04)처럼 10점 만점 중 9~10점을 받을 수 있도록 완벽하게 다시 작성해 주세요.

규칙:
1. 카테고리: {cat} ({cat_name}), 제목: {candidate.title}
2. {rule_detail}
3. 분량: 공백 포함 650~950자 한국어 산문으로 작성할 것.
4. 다른 설명이나 인사말 없이 오직 보강된 본문 텍스트만 출력할 것.
5. {"AI 창작물은 허구의 사연이다. 실화·실제 제보·외부 게시물이라고 주장하거나 출처 URL을 만들지 않는다." if candidate.ai_generated else "제공된 출처 메타데이터를 새로 만들거나 바꾸지 않는다."}

원문:
{candidate.text}"""

    def evaluate(self, candidate: CandidateSource) -> Tuple[int, str]:
        require_safe_candidate(candidate)
        if self.mock:
            # Deterministic mock scoring based on text length and structure
            has_quotes = '"' in candidate.text or '“' in candidate.text
            is_story = candidate.category in {"C01", "C02", "C03", "C04", "C05"}
            if len(candidate.text) >= 550 and (not is_story or has_quotes):
                return 9, "Mock 평가 통과: 구체적 서사와 오디오 몰입도 우수"
            return 7, "Mock 평가 미달: 분량 또는 대화 디테일 보강 필요"

        prompt = self.build_eval_prompt(candidate) + PRIVACY_PROMPT
        try:
            res = subprocess.run(
                ["agy", "--model", self.model, "--dangerously-skip-permissions", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            out = res.stdout.strip()
            m = re.search(r"SCORE:\s*(\d+)\s*\|\s*REASON:\s*(.+)", out)
            if m:
                require_private_data_free(m.group(2))
                return int(m.group(1)), m.group(2).strip()
            m2 = re.search(r"(\d+)\s*점", out)
            if m2:
                require_private_data_free(out)
                return int(m2.group(1)), out[:120].replace("\n", " ")
        except PrivacyViolation:
            raise
        except subprocess.TimeoutExpired:
            return 0, f"Evaluation timed out after {self.timeout}s"
        except Exception:
            return 0, "Evaluation transport error"
        return 0, "Could not parse SCORE output from headless session"

    def elevate(self, candidate: CandidateSource, rejection_reason: str) -> str:
        require_safe_candidate(candidate)
        if self.mock:
            extra = (
                '\n\n"정말 이대로 포기하실 겁니까?" 그 한마디가 정적을 갈랐습니다. '
                '수많은 시행착오 끝에 밝혀진 98.4%의 정밀 데이터와 현장의 반전은 '
                '우리 일상에 깊은 울림과 명쾌한 해답을 남깁니다.'
            )
            base = candidate.text
            while len(base) < 680:
                base += extra
            return base

        require_private_data_free(rejection_reason)
        prompt = self.build_elevate_prompt(candidate, rejection_reason) + PRIVACY_PROMPT
        try:
            res = subprocess.run(
                ["agy", "--model", self.model, "--dangerously-skip-permissions", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            out = res.stdout.strip()
            out = re.sub(r"^#+\s+.*\n+", "", out).strip()
            if len(out) >= 450:
                require_private_data_free(out)
                return out
        except PrivacyViolation:
            raise
        except Exception:
            pass
        return candidate.text

    def evaluate_and_ensure(self, candidate: CandidateSource) -> CandidateSource:
        """Evaluate candidate and automatically elevate up to max_retries until score >= min_score."""
        score, reason = self.evaluate(candidate)
        candidate.score = score
        candidate.eval_reason = reason
        require_safe_candidate(candidate)

        attempt = 0
        while candidate.score < self.min_score and attempt < self.max_retries:
            attempt += 1
            elevated_text = self.elevate(candidate, candidate.eval_reason or "몰입도 및 구체적 수치·대화 보강 필요")
            candidate.text = elevated_text
            # Update summary from first 2 sentences
            sents = [s.strip() for s in elevated_text.replace("\n", " ").split(".") if s.strip()]
            if len(sents) >= 2:
                candidate.summary = (sents[0] + ". " + sents[1] + ".")[:145]
            score, reason = self.evaluate(candidate)
            candidate.score = score
            candidate.eval_reason = reason
            require_safe_candidate(candidate)

        return candidate
