# AI 창작 사연 출처 정책 · 2026-11-13

C01~C05의 생성 경로는 AI가 새로 쓰는 허구의 사연이다. 외부 출처·Instagram URL·원천목록 ID·attribution을 만들지 않는다. Markdown은 source_url=null, source_kind=original(자체 작성 입력), attribution=""이며 AI 창작임을 notes에 명시한다. 원장은 URL과 원천목록 ID를 비우고 원천 명칭을 AI 창작물로 표시한다. 웹페이지 수집·대조 완료나 실화라고 기록하지 않는다. 이 지침은 아래의 Instagram 실원문 필수 안내보다 우선한다. 실제 수집 자료의 출처는 유지하며 기존 산출물은 소급 변경하지 않는다. 품질·개인정보 검사는 유지한다.

# 원본 저장 경로 · 2026-11-10 (아래 과거 경로보다 우선)

원본 보관소는 이 수집기 내부 `output/<batch_id>/`다. staging/sandbox는 원본을 한 곳에만 저장하며 good 사본을 만들지 않는다. direct는 원본 외에 script_agent/input에 임시 대기 사본을 둔다. 기존 입력을 archive로 이동하거나 백업하지 않는다. script_agent는 처리 성공 후 임시 입력을 제거하고 상태만 state/completed에 저장한다. 개인정보·품질·권리 기준은 그대로 유지한다.

# Source Collector Workspace Instructions (`AGENTS.md`)

## 개인정보 차단 계약 · 2026-11-10

이 계약은 아래 과거의 계정명 필수 지침보다 우선합니다. 사연형(C01~C05)의
`author`는 `익명`만 허용합니다. 생성 응답이 다른 작성자를 반환하면 후보 전체를
거부하며 계정명을 자동으로 만들거나 임의로 삭제해 통과시키지 않습니다.
출처 확인용 Instagram `/p/<shortcode>/`, `/reel/<shortcode>/` URL은 허용하지만
프로필 URL과 계정 추적 쿼리는 차단합니다. 게시물 링크를 따라가면 작성자를
확인할 수 있으므로 이 정책은 완전한 비식별화를 보장하지 않습니다.

`privacy.py`는 모든 후보 필드와 중첩 메타데이터에서 핸들, 계정명 표기,
SNS 프로필 링크, 이메일, 한국 전화번호·주민등록번호 형식을 검사합니다.
전각 문자, 제로폭 문자, HTML/URL 인코딩을 정규화한 뒤 검사합니다.
생성·재작성·심사 프롬프트에 금지 규칙을 넣고, 응답 수신 후 및 저장 전에
코드 검사로 강제합니다. 모델이 이미 반환한 식별정보의 생성 자체를 막는
보장은 없지만, 탐지 후보의 다음 심사 전송·Markdown/CSV/수집 로그 저장은
막습니다. 품질 점수가 높아도 예외가 없으며 원문 값 대신 규칙명만 진단합니다.
`run`은 해당 후보를 탈락시키고 기존 보충 한도 내에서만 보충합니다.
`evaluate`는 전체 입력 메타데이터도 검사하며 차단 시 종료 코드 2를 반환합니다.

이는 형식 기반 검사입니다. 표지 없는 닉네임, 자연어 실명·주소, 새로운 우회
표기나 알려지지 않은 플랫폼을 완전히 판별하지 못합니다. 공적 인물·기관명은
일괄 제거하지 않습니다. 개인정보 전체에 대한 무검출 보증이나 사람의 최종
검토를 대체하지 않습니다. 기존 원문·원장·API 제공자/agy 자체 기록은 소급
정리하지 않습니다. 실행 중인 프로세스는 재시작 후 새 검사를 사용합니다.
출처 실재·이용 조건 확인 및 저작자 표시 의무는 별도이며, 익명 표시와 권리
조건이 충돌하는 자료를 사용 가능으로 간주하지 않습니다.


이 디렉터리(`/path/to/hyundai_pleos_app/ai/source_collector`)는 **오디오 숏폼 원천 소스 수집 및 헤드리스 AI 평가 전용 하네스 워크스페이스**입니다.
사용자가 이 폴더에서 Antigravity CLI(`agy`)를 열고 소스 수집을 요청하면, 별도의 파일 태그(`@...`)가 없더라도 즉시 본 지침과 [`SOURCE_COLLECTION_GUIDE.md`](SOURCE_COLLECTION_GUIDE.md)를 따라 작업을 수행하세요.

---

## 1. 기본 참조 파일 (현재 폴더 내 완비)
- 수집 표준 가이드: [`SOURCE_COLLECTION_GUIDE.md`](SOURCE_COLLECTION_GUIDE.md)
- 마스터 소스 원장: [`전체_소스_목록.csv`](전체_소스_목록.csv) (상위 `../script_agent/전체_소스_목록.csv`와 자동 동기화됨)
- 공인 출처 카탈로그 (C06~C21): [`data/원천목록.csv`](data/원천목록.csv)
- 골드 스탠다드 벤치마크 및 기존 데모: [`data/컨텐츠 제작 관리.txt`](data/%EC%BB%A8%ED%85%90%EC%B8%A0%20%EC%A0%9C%EC%9E%91%20%EA%B4%80%EB%A6%AC.txt)

---

## 2. 하네스 CLI 도구 (`./collector`)

이 워크스페이스에는 수집·중복검사·헤드리스 평가·스키마 검증·원장 등록을 수행하는 `./collector` CLI가 구축되어 있습니다.

### 주요 명령어
1. **현황 확인**:
   ```bash
   ./collector status
   ```
2. **엔드투엔드 자동 수집 및 헤드리스 평가 실행**:
   - **Staging 모드 (기본 권장, `../script_agent/input/`을 건드리지 않고 `output/<batch_id>/`에 저장)**:
     ```bash
     ./collector run --mode staging --categories all --count 20
     ```
   - **Direct 모드 (기존 입력 보존 후 신규 실행 사본만 `input/`에 저장)**:
     ```bash
     ./collector run --mode direct --categories all --count 20
     ```
   - **특정 카테고리 지정 수집**:
     ```bash
     ./collector run --mode staging --categories C01,C02,C13 --count 5
     ```
   - **Sandbox 테스트 모드 (프로덕션 원장/폴더 변경 없이 격리 테스트)**:
     ```bash
     ./collector run --mode sandbox --categories C01 --count 1 --json
     ```
3. **개별 제목/URL 중복 사전 검사 (`prescreen`)**:
   ```bash
   ./collector prescreen --title "검사할 제목" --url "https://..." --category C01
   ```
4. **개별 Markdown 파일 헤드리스 평가 및 자동 승급 (`evaluate`)**:
   ```bash
   ./collector evaluate <파일경로>.md --elevate
   ```

---

## 3. 필수 준수 원칙
1. **중복 금지**: `전체_소스_목록.csv` 및 `data/컨텐츠 제작 관리.txt`에 이미 존재하는 주제·URL과 절대 겹치지 않아야 합니다 (`./collector prescreen` 활용).
2. **출처 원칙**:
   - `C01 ~ C05`: 인스타그램 실원문 URL(`https://www.instagram.com/p/...`)과 익명 작성자 표기 필수, 생생한 대화·위기·반전이 살아있는 1인칭 사연/썰 형태.
   - `C06 ~ C21`: `data/원천목록.csv` 공인 출처 기반, 구체적 연도·실명·통계 수치·작동 메커니즘·일상 통찰 포함.
3. **품질 커트라인 (9점 이상)**:
   - 모든 소스는 `gemini-3.8-flash-medium` 헤드리스 평가에서 **9점 이상(`SCORE >= 9`)**을 받아야 하며, 9점 미만은 즉시 드랍/재작성합니다.
4. **검증 및 원장 동기화**:
   - 모든 채택 파일은 `script-agent validate-input` 검증(`valid: true`)을 통과해야 하며, `전체_소스_목록.csv` 및 `../script_agent/전체_소스_목록.csv`에 15개 정규 컬럼(UTF-8-SIG)으로 기록되어야 합니다.
