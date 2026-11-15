"""Configuration and constants for the Source Collection Harness."""

from pathlib import Path

# Paths
SOURCE_COLLECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = SOURCE_COLLECTOR_ROOT.parent.parent
SCRIPT_AGENT_ROOT = PROJECT_ROOT / "ai" / "script_agent"

# Master CSVs
DEFAULT_CSV_KO = SCRIPT_AGENT_ROOT / "전체_소스_목록.csv"
DEFAULT_CSV_EN = SCRIPT_AGENT_ROOT / "all_sources_summary.csv"

# References
DOCS_CSV = PROJECT_ROOT.parent / "docs" / "원천목록.csv"
if not DOCS_CSV.exists():
    DOCS_CSV = SOURCE_COLLECTOR_ROOT / "data" / "원천목록.csv"

DEMO_TXT = PROJECT_ROOT.parent / "demo" / "컨텐츠 제작 관리.txt"
if not DEMO_TXT.exists():
    DEMO_TXT = SOURCE_COLLECTOR_ROOT / "data" / "컨텐츠 제작 관리.txt"

SOURCE_GUIDE = SCRIPT_AGENT_ROOT / "SOURCE_COLLECTION_GUIDE.md"
if not SOURCE_GUIDE.exists():
    SOURCE_GUIDE = SOURCE_COLLECTOR_ROOT / "SOURCE_COLLECTION_GUIDE.md"

VALIDATE_CLI = SCRIPT_AGENT_ROOT / ".venv" / "bin" / "script-agent"

# Categories
CATEGORIES = {
    "C01": {"name": "일·직장", "type": "창작·각색형", "source_id": "", "source_name": "AI 창작물"},
    "C02": {"name": "연애·결혼", "type": "창작·각색형", "source_id": "", "source_name": "AI 창작물"},
    "C03": {"name": "가족·육아", "type": "창작·각색형", "source_id": "", "source_name": "AI 창작물"},
    "C04": {"name": "친구·인간관계", "type": "창작·각색형", "source_id": "", "source_name": "AI 창작물"},
    "C05": {"name": "마음·습관·성장", "type": "창작·각색형", "source_id": "", "source_name": "AI 창작물"},
    "C06": {"name": "자취·살림·생활팁", "type": "사실해설형", "source_id": "SRC-016", "source_name": "대한민국 정책브리핑"},
    "C07": {"name": "음식·맛집·식문화", "type": "사실해설형", "source_id": "SRC-055", "source_name": "한국어 위키백과"},
    "C08": {"name": "돈·소비·생활경제", "type": "사실해설형", "source_id": "SRC-016", "source_name": "대한민국 정책브리핑"},
    "C09": {"name": "건강·운동·생활리듬", "type": "사실해설형", "source_id": "SRC-001", "source_name": "U.S. CDC"},
    "C10": {"name": "반려동물·자연", "type": "사실해설형", "source_id": "SRC-032", "source_name": "U.S. National Park Service"},
    "C11": {"name": "여행·지역·문화", "type": "사실해설형", "source_id": "SRC-032", "source_name": "U.S. National Park Service"},
    "C12": {"name": "패션·뷰티·자기표현", "type": "사실해설형", "source_id": "SRC-076", "source_name": "식품의약품안전처"},
    "C13": {"name": "과학·기술·AI", "type": "사실해설형", "source_id": "SRC-005", "source_name": "NASA Image and Video Library"},
    "C14": {"name": "역사·인물", "type": "사실해설형", "source_id": "SRC-030", "source_name": "국립중앙박물관"},
    "C15": {"name": "언어·독서·인문", "type": "사실해설형", "source_id": "SRC-050", "source_name": "국립국어원"},
    "C16": {"name": "뉴스·사회·시사", "type": "사실해설형", "source_id": "SRC-016", "source_name": "대한민국 정책브리핑"},
    "C17": {"name": "사건·미스터리·추리", "type": "사실해설형", "source_id": "SRC-055", "source_name": "한국어 위키백과"},
    "C18": {"name": "음악·영화·예술", "type": "사실해설형", "source_id": "SRC-027", "source_name": "The Met Open Access"},
    "C19": {"name": "스포츠·게임·취미", "type": "사실해설형", "source_id": "SRC-070", "source_name": "World Athletics"},
    "C20": {"name": "자동차·교통·이동", "type": "사실해설형", "source_id": "SRC-016", "source_name": "대한민국 정책브리핑"},
    "C21": {"name": "성경·말씀·신앙", "type": "해석·감상형", "source_id": "SRC-015", "source_name": "World English Bible"}
}

# Standard Licenses
LICENSES = {
    "ai_creation": {
        "kind": "팀 자체 AI 창작물", "version": None,
        "commercial": True, "modify": True, "share_alike": False, "url": None,
        "evidence": "AI가 새로 작성한 허구의 사연. 팀 내부 대본 제작용이며 외부 출처나 수집·대조 기록이 없음. 제3자에 대한 자유이용 허락이 아님.",
    },
    "instagram": {
        "kind": "인스타그램 공개 게시글 (창작·각색형)",
        "version": None,
        "commercial": True,
        "modify": True,
        "share_alike": False,
        "url": None,
        "evidence": "인스타그램 공개 게시글 원문 및 이용조건 확인; 숏폼 오디오 사연 각색 목적 재구성"
    },
    "korea_kr": {
        "kind": "공공누리 제1유형",
        "version": "1",
        "commercial": True,
        "modify": True,
        "share_alike": False,
        "url": "https://www.kogl.or.kr/info/licenseType1.do",
        "evidence": "대한민국 정책브리핑 공공저작물 자유이용 및 출처표시 기준 준수; 텍스트 공공누리 제1유형"
    },
    "wikipedia": {
        "kind": "CC BY-SA 4.0",
        "version": "4.0",
        "commercial": True,
        "modify": True,
        "share_alike": True,
        "url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "evidence": "Wikimedia 공식 rightsinfo의 CC BY-SA 4.0; 텍스트 CC BY-SA 4.0 적용 및 출처 표시"
    },
    "cdc": {
        "kind": "U.S. Government public-domain text",
        "version": None,
        "commercial": True,
        "modify": True,
        "share_alike": False,
        "url": "https://www.cdc.gov/other/agencymaterials.html",
        "evidence": "U.S. CDC 공공 도메인 저작물 무료 이용 가이드라인 확인"
    },
    "nasa": {
        "kind": "U.S. Government public-domain text",
        "version": None,
        "commercial": True,
        "modify": True,
        "share_alike": False,
        "url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
        "evidence": "NASA Image and Media Policy에 따른 퍼블릭 도메인 저작물"
    },
    "nps": {
        "kind": "U.S. Government public-domain text",
        "version": None,
        "commercial": True,
        "modify": True,
        "share_alike": False,
        "url": "https://www.nps.gov/aboutus/disclaimer.htm",
        "evidence": "U.S. National Park Service 퍼블릭 도메인 저작물 이용"
    },
    "the_met": {
        "kind": "CC0 1.0 Universal",
        "version": "1.0",
        "commercial": True,
        "modify": True,
        "share_alike": False,
        "url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "evidence": "메트로폴리탄 미술관 Open Access 정책에 따른 CC0 퍼블릭 도메인 기증"
    },
    "ebible": {
        "kind": "Public Domain",
        "version": None,
        "commercial": True,
        "modify": True,
        "share_alike": False,
        "url": "https://ebible.org/eng-web/copyright.htm",
        "evidence": "eBible 공식 저작권 안내의 Public Domain 및 상업적 사용·방송·수정 허용 확인."
    }
}

CSV_FIELDNAMES = [
    "소스ID", "카테고리_코드", "카테고리명", "주제_제목",
    "원천목록_ID", "원천_명칭", "작성자_발행처", "원본_URL",
    "간략_주제_요약", "제작유형", "라이선스", "본문_글자수",
    "수집일시", "AI_평가점수", "AI_심사평"
]
