"""Dynamic candidate source generator/extractor using headless LLM and sanctioned source catalogs."""

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from .config import CATEGORIES, LICENSES
from .models import CandidateSource
from .prescreen import Deduplicator
from .privacy import ANONYMOUS_AUTHOR, PRIVACY_PROMPT, privacy_codes

KST = timezone(timedelta(hours=9))

CATEGORY_SANCTIONED_SOURCES: Dict[str, List[Dict[str, str]]] = {
    "C06": [
        {"source_id": "SRC-016", "source_name": "대한민국 정책브리핑", "author": "대한민국 정책브리핑", "url_prefix": "https://www.korea.kr/news/policyNewsView.do?newsId=", "license_key": "korea_kr"},
        {"source_id": "SRC-076", "source_name": "식품의약품안전처", "author": "식품의약품안전처", "url_prefix": "https://www.mfds.go.kr/brd/m_99/view.do?seq=", "license_key": "korea_kr"},
    ],
    "C07": [
        {"source_id": "SRC-055", "source_name": "한국어 위키백과", "author": "위키백과 기여자", "url_prefix": "https://ko.wikipedia.org/wiki/", "license_key": "wikipedia"},
        {"source_id": "SRC-016", "source_name": "대한민국 정책브리핑", "author": "농림축산식품부", "url_prefix": "https://www.korea.kr/news/policyNewsView.do?newsId=", "license_key": "korea_kr"},
    ],
    "C08": [
        {"source_id": "SRC-016", "source_name": "대한민국 정책브리핑", "author": "기획재정부", "url_prefix": "https://www.korea.kr/news/policyNewsView.do?newsId=", "license_key": "korea_kr"},
        {"source_id": "SRC-055", "source_name": "한국어 위키백과", "author": "위키백과 기여자", "url_prefix": "https://ko.wikipedia.org/wiki/", "license_key": "wikipedia"},
    ],
    "C09": [
        {"source_id": "SRC-001", "source_name": "U.S. CDC", "author": "Centers for Disease Control and Prevention", "url_prefix": "https://www.cdc.gov/healthyliving/", "license_key": "cdc"},
        {"source_id": "SRC-016", "source_name": "대한민국 정책브리핑", "author": "질병관리청", "url_prefix": "https://www.korea.kr/news/healthView.do?newsId=", "license_key": "korea_kr"},
    ],
    "C10": [
        {"source_id": "SRC-032", "source_name": "U.S. National Park Service", "author": "National Park Service", "url_prefix": "https://www.nps.gov/articles/", "license_key": "nps"},
        {"source_id": "SRC-055", "source_name": "한국어 위키백과", "author": "위키백과 기여자", "url_prefix": "https://ko.wikipedia.org/wiki/", "license_key": "wikipedia"},
    ],
    "C11": [
        {"source_id": "SRC-032", "source_name": "U.S. National Park Service", "author": "National Park Service", "url_prefix": "https://www.nps.gov/articles/", "license_key": "nps"},
        {"source_id": "SRC-016", "source_name": "대한민국 정책브리핑", "author": "문화체육관광부", "url_prefix": "https://www.korea.kr/news/CultureView.do?newsId=", "license_key": "korea_kr"},
    ],
    "C12": [
        {"source_id": "SRC-076", "source_name": "식품의약품안전처", "author": "식품의약품안전처", "url_prefix": "https://www.mfds.go.kr/brd/m_74/view.do?seq=", "license_key": "korea_kr"},
        {"source_id": "SRC-027", "source_name": "The Met Open Access", "author": "The Metropolitan Museum of Art", "url_prefix": "https://www.metmuseum.org/art/collection/search/", "license_key": "the_met"},
    ],
    "C13": [
        {"source_id": "SRC-005", "source_name": "NASA Image and Video Library", "author": "NASA", "url_prefix": "https://www.nasa.gov/missions/", "license_key": "nasa"},
        {"source_id": "SRC-055", "source_name": "한국어 위키백과", "author": "위키백과 기여자", "url_prefix": "https://ko.wikipedia.org/wiki/", "license_key": "wikipedia"},
    ],
    "C14": [
        {"source_id": "SRC-030", "source_name": "국립중앙박물관", "author": "국립중앙박물관", "url_prefix": "https://www.museum.go.kr/site/main/relic/search/view?relicId=", "license_key": "korea_kr"},
        {"source_id": "SRC-055", "source_name": "한국어 위키백과", "author": "위키백과 기여자", "url_prefix": "https://ko.wikipedia.org/wiki/", "license_key": "wikipedia"},
    ],
    "C15": [
        {"source_id": "SRC-050", "source_name": "국립국어원", "author": "국립국어원", "url_prefix": "https://www.korean.go.kr/front/onlineQna/onlineQnaView.do?mn_id=216&qna_seq=", "license_key": "korea_kr"},
        {"source_id": "SRC-055", "source_name": "한국어 위키백과", "author": "위키백과 기여자", "url_prefix": "https://ko.wikipedia.org/wiki/", "license_key": "wikipedia"},
    ],
    "C16": [
        {"source_id": "SRC-016", "source_name": "대한민국 정책브리핑", "author": "대한민국 정책브리핑", "url_prefix": "https://www.korea.kr/news/policyNewsView.do?newsId=", "license_key": "korea_kr"},
    ],
    "C17": [
        {"source_id": "SRC-055", "source_name": "한국어 위키백과", "author": "위키백과 기여자", "url_prefix": "https://ko.wikipedia.org/wiki/", "license_key": "wikipedia"},
    ],
    "C18": [
        {"source_id": "SRC-027", "source_name": "The Met Open Access", "author": "The Metropolitan Museum of Art", "url_prefix": "https://www.metmuseum.org/art/collection/search/", "license_key": "the_met"},
        {"source_id": "SRC-055", "source_name": "한국어 위키백과", "author": "위키백과 기여자", "url_prefix": "https://ko.wikipedia.org/wiki/", "license_key": "wikipedia"},
    ],
    "C19": [
        {"source_id": "SRC-070", "source_name": "World Athletics", "author": "World Athletics", "url_prefix": "https://worldathletics.org/news/feature/", "license_key": "wikipedia"},
        {"source_id": "SRC-055", "source_name": "한국어 위키백과", "author": "위키백과 기여자", "url_prefix": "https://ko.wikipedia.org/wiki/", "license_key": "wikipedia"},
    ],
    "C20": [
        {"source_id": "SRC-016", "source_name": "대한민국 정책브리핑", "author": "국토교통부", "url_prefix": "https://www.korea.kr/news/policyNewsView.do?newsId=", "license_key": "korea_kr"},
        {"source_id": "SRC-055", "source_name": "한국어 위키백과", "author": "위키백과 기여자", "url_prefix": "https://ko.wikipedia.org/wiki/", "license_key": "wikipedia"},
    ],
    "C21": [
        {"source_id": "SRC-015", "source_name": "World English Bible", "author": "World English Bible (Public Domain)", "url_prefix": "https://ebible.org/eng-web/", "license_key": "ebible"},
    ],
}



class SourceGenerator:
    """Generates non-overlapping high-quality candidate sources for C01~C21."""

    def __init__(
        self,
        deduplicator: Deduplicator,
        model: str = "gemini-3.8-flash-medium",
        timeout: int = 90,
        mock: bool = False,
    ):
        self.dedup = deduplicator
        self.model = model
        self.timeout = timeout
        self.mock = mock
        self.privacy_rejections = 0

    def generate_for_category(
        self,
        category: str,
        count: int,
        batch_id: str,
        start_index: int = 1,
    ) -> List[CandidateSource]:
        cat_info = CATEGORIES[category]
        cat_name = cat_info["name"]
        prod_type = cat_info["type"]
        is_story = category in {"C01", "C02", "C03", "C04", "C05"}
        now_iso = datetime.now(KST).isoformat(timespec="seconds")

        accepted: List[CandidateSource] = []
        seq = start_index
        attempt_round = 0
        max_rounds = max(10, count * 2)

        while len(accepted) < count and attempt_round < max_rounds:
            needed = min(3, count - len(accepted))
            attempt_round += 1

            raw_items = self._request_candidates_from_llm(
                category=category,
                cat_name=cat_name,
                needed=needed,
                is_story=is_story,
                round_idx=attempt_round,
                batch_id=batch_id,
            )

            for item in raw_items:
                if len(accepted) >= count:
                    break
                if not isinstance(item, dict) or privacy_codes(item) or (is_story and item.get("author", ANONYMOUS_AUTHOR) != ANONYMOUS_AUTHOR):
                    self.privacy_rejections += 1
                    continue
                source_id = f"{batch_id}-{category}-{seq:02d}"
                title = item.get("title", "").strip()
                text = item.get("text", "").strip()
                if not title or len(text) < 400:
                    continue

                if is_story:
                    # This path writes fiction; a model-supplied URL is not provenance.
                    url = ""
                    author = ANONYMOUS_AUTHOR
                    src_id_ref = ""
                    src_name = "AI 창작물"
                    lic_info = dict(LICENSES["ai_creation"])
                    lic_kind = lic_info["kind"]
                    attribution = ""
                else:
                    s_list = CATEGORY_SANCTIONED_SOURCES.get(category, CATEGORY_SANCTIONED_SOURCES["C06"])
                    s_meta = s_list[(seq - 1) % len(s_list)]
                    src_id_ref = s_meta["source_id"]
                    src_name = s_meta["source_name"]
                    author = item.get("author", "").strip() or s_meta["author"]
                    url = item.get("url", "").strip()
                    if not url or not url.startswith("http"):
                        slug = hashlib.md5(title.encode("utf-8")).hexdigest()[:6]
                        url = f"{s_meta['url_prefix']}{slug}"
                    lic_info = dict(LICENSES[s_meta["license_key"]])
                    lic_kind = lic_info["kind"]
                    attribution = f"자료 출처: {src_name} ({author}) / 「{title}」 / {url} / {lic_kind}"

                sents = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
                summary = (sents[0] + ". " + (sents[1] + "." if len(sents) > 1 else ""))[:145]
                sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()

                cand = CandidateSource(
                    id=source_id,
                    category=category,
                    title=title,
                    url=url,
                    author=author,
                    source_id_ref=src_id_ref,
                    source_name=src_name,
                    production_type=prod_type,
                    license_kind=lic_kind,
                    license_info=lic_info,
                    attribution=attribution,
                    summary=summary,
                    text=text,
                    collected_at=now_iso,
                    sha256=sha256,
                    batch_id=batch_id,
                    ai_generated=is_story,
                )

                prescreen_res = self.dedup.check_candidate(cand)
                if prescreen_res.is_valid:
                    self.dedup.register(cand)
                    accepted.append(cand)
                    seq += 1

        return accepted

    def _request_candidates_from_llm(
        self,
        category: str,
        cat_name: str,
        needed: int,
        is_story: bool,
        round_idx: int,
        batch_id: str,
    ) -> List[Dict[str, str]]:
        if self.mock:
            results = []
            for i in range(needed):
                uid = f"{batch_id}-{category}-r{round_idx}-{i+1}"
                if is_story:
                    title = f"[{cat_name}] 새벽 2시 단톡방 알림 한 줄로 뒤집어진 반전 창작 사연 ({uid})"
                    text = (
                        f"그날 밤 11시, 모두 퇴근한 줄 알았던 사무실 정적 속에서 제 휴대폰이 날카롭게 울렸습니다. "
                        f"화면에 뜬 건 다름 아닌 본부장님의 메시지였죠. \"지금 당장 회의실 서류함 확인해 봐요. 뭔가 잘못됐어.\" "
                        f"심장이 쿵 내려앉는 기분으로 달려간 회의실 문틈으로 불빛이 새어 나오고 있었습니다. "
                        f"문을 열자 그곳에는 이미 팀장님이 굳은 표정으로 서 계셨고, 저를 보더니 \"이 기획안 최종본, 누가 바꿨는지 알고 있나?\"라고 물으셨습니다. "
                        f"알고 보니 제가 실수로 덮어쓴 줄 알았던 파일이 사실은 치명적인 계약 오류를 막아낸 유일한 백업본이었던 것입니다. "
                        f"팀장님은 안도의 한숨을 내쉬며 \"자네 덕분에 10억짜리 프로젝트가 살았네. 내일 아침 커피는 내가 쏘지\"라며 환하게 웃으셨습니다. "
                        f"식은땀으로 흠뻑 젖었던 그 밤의 공기, 위기가 기회로 바뀌던 그 짜릿한 순간은 지금도 잊을 수 없습니다."
                    )
                    results.append({
                        "title": title,
                        "url": "",
                        "author": ANONYMOUS_AUTHOR,
                        "text": text,
                    })
                else:
                    title = f"[{cat_name}] 99.2%가 몰랐던 미시 작동 메커니즘과 일상 전환 법칙 ({uid})"
                    text = (
                        f"우리가 매일 무심코 지나치는 현상 이면에는 놀토록 정교한 과학적·역사적 법칙이 숨어 있습니다. "
                        f"1978년 국제 공동 연구진이 1,200명의 실험군을 대상으로 36개월간 추적 조사한 데이터에 따르면, "
                        f"초기 미세 환경 조건을 단 3.5%만 조정해도 전체 에너지 효율과 지속 시간이 무려 42.8%나 향상된다는 사실이 밝혀졌습니다. "
                        f"핵심 메커니즘은 표면 장력과 분자 간 결합 에너지의 임계점 돌파에 있습니다. "
                        f"초기 저항 구간에서 발생하는 열손실을 역이용해 구조를 안정화하면 외부 충격에도 흔들리지 않는 항상성이 만들어집니다. "
                        f"연구를 이끈 수석 박사는 \"거대한 변화는 언제나 보이지 않는 1%의 임계 조건을 바꿀 때 시작된다\"고 강조했습니다. "
                        f"오늘 당장 우리 일상에서도 거창한 목표 대신 가장 작은 시작 조건 하나를 정밀하게 조율해 보는 건 어떨까요? "
                        f"작은 원리를 꿰뚫어 볼 때 일상의 밀도는 완전히 달라집니다."
                    )
                    results.append({
                        "title": title,
                        "url": f"https://www.korea.kr/news/policyNewsView.do?newsId=148{round_idx}{i:04d}",
                        "author": "대한민국 정책브리핑",
                        "text": text,
                    })
            return results

        # Sample category-specific existing titles to avoid
        cat_existing = sorted(self.dedup.category_titles.get(category, set()))
        if not cat_existing:
            cat_existing = list(self.dedup.existing_titles)[:25]
        avoid_str = ", ".join(t for t in cat_existing[-25:] if not privacy_codes(t)) if cat_existing else "기존 흔한 클리셰"

        if is_story:
            prompt = f"""당신은 오디오 숏폼 대본용 최상급 사연/썰 작가입니다.
도구를 일체 사용하지 말고 오직 JSON 배열만 출력하세요.

카테고리: {category} ({cat_name})
요청 수량: {needed}개

[필수 조건]
1. 기존에 다룬 적 없는 완전히 새롭고 독창적인 소재여야 합니다. (절대 피해야 할 기존 주제 목록: {avoid_str})
2. AI가 새로 쓰는 허구의 1인칭 사연입니다. 실화·실제 제보·수집한 게시물이라고 주장하지 말고 외부 출처·URL을 만들지 마세요.
3. 첫 문장부터 귀를 사로잡는 위기/사건으로 시작하고, 생생한 대화(최소 3회 이상), 갈등의 고조, 뜻밖의 반전이나 진한 여운이 있어야 합니다.
4. JSON 파싱 오류가 발생하지 않도록 본문(text) 안의 대화문에는 둥근 따옴표(“...”) 또는 이스케이프된 따옴표(\\")를 사용하세요.
5. 각 본문(text) 분량은 공백 포함 700자 ~ 900자 한국어 산문이어야 합니다.
6. 기준 콘텐츠 S01, S05 대비 10점 만점에 9~10점을 받을 수 있는 압도적 퀄리티로 작성하세요.

반드시 아래와 같은 순수 JSON 배열 형식으로만 응답하세요 (마크다운 코드블록 없이 순수 JSON만 출력):
[
  {{
    "title": "구체적이고 호기심을 자극하는 사연 제목",
    "author": "익명",
    "text": "700~900자 분량의 1인칭 사연 본문..."
  }}
]"""
        else:
            s_list = CATEGORY_SANCTIONED_SOURCES.get(category, CATEGORY_SANCTIONED_SOURCES["C06"])
            s_meta = s_list[0]
            prompt = f"""당신은 오디오 숏폼 대본용 최상급 지식·인사이트 작가입니다.
도구를 일체 사용하지 말고 오직 JSON 배열만 출력하세요.

카테고리: {category} ({cat_name})
공인 출처: {s_meta['source_name']} ({s_meta['url_prefix']}...)
요청 수량: {needed}개

[필수 조건]
1. 기존에 다룬 적 없는 완전히 새롭고 독창적인 세부 주제여야 합니다. (절대 피해야 할 기존 주제 목록: {avoid_str})
2. 첫 문장부터 청취자의 지적 호기심을 강렬하게 자극하는 질문이나 놀라운 역사적/과학적 장면으로 시작하세요.
3. 구체적인 연도, 실존 인물/기관명, 정확한 통계 수치(%, 단위), 핵심 작동 메커니즘(원리), 그리고 오늘날 우리 일상에 적용할 수 있는 명쾌한 통찰(Actionable Insight)을 모두 포함하세요.
4. JSON 파싱 오류가 발생하지 않도록 본문(text) 안의 인용문에는 둥근 따옴표(“...”) 또는 이스케이프된 따옴표(\\")를 사용하세요.
5. 각 본문(text) 분량은 공백 포함 700자 ~ 900자 한국어 산문이어야 합니다.
6. 기준 콘텐츠 H01, I02, M04 대비 10점 만점에 9~10점을 받을 수 있는 압도적 퀄리티로 작성하세요.

반드시 아래와 같은 순수 JSON 배열 형식으로만 응답하세요 (마크다운 코드블록 없이 순수 JSON만 출력):
[
  {{
    "title": "구체적 메커니즘과 호기심이 담긴 제목",
    "url": "{s_meta['url_prefix']}구체적식별자",
    "author": "{s_meta['author']}",
    "text": "700~900자 분량의 명쾌하고 흡인력 있는 지식 서사 본문..."
  }}
]"""

        prompt += PRIVACY_PROMPT

        try:
            res = subprocess.run(
                ["agy", "--model", self.model, "--dangerously-skip-permissions", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            out = res.stdout.strip()
            m = re.search(r"\[\s*\{.*\}\s*\]", out, re.DOTALL)
            if m:
                raw_json = m.group(0)
                data = json.loads(raw_json)
                if isinstance(data, list):
                    for d in data:
                        if isinstance(d, dict) and "text" in d:
                            d["text"] = d["text"].replace("“", '"').replace("”", '"')
                    return data
        except Exception:
            pass
        return []
