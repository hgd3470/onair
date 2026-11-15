## 로컬 모델 설정 · 2026-11-13

현재 기본값은 `gemini / gemini-4-hydrogen / medium`, `[collection] mode = "batch"`다. 주제 선정·사실/의견 추출과 재추출·Google Search 보충 검색을 Gemini Batch API로 제출한다. OpenAI 검증은 기존 `gpt-6-luna / medium` 실시간 호출을 유지한다. Gemini 배치에는 `gemini_api_key`가 필요하며 함께 있는 서비스 계정 설정으로 인증을 바꾸지 않는다.

명령은 그대로 `./collect_to_inbox.sh --limit 5` 또는 `bash tools/run.sh collect`를 사용한다. `--mode standard`로 명시할 때만 실시간 경로를 선택한다. Vertex는 standard 모드의 선택적 호환 경로이며, 기존 서비스 계정 파일과 `credentials_root` 설정은 보존한다. `.env`는 여전히 수집기 루트에서만 읽는다. 아래 과거 실행은 당시 모델·인증의 기록이며 현재 Gemini 배치의 실제 API 품질 검증을 뜻하지 않는다.

배치 조회 주기와 한 원격 작업의 대기 상한은 `[batch] poll_interval_sec=15`, `wait_timeout_sec=86400`이다. 대기 상한은 공급자 작업을 취소하지 않는다. `pending` 결과는 같은 날짜·실행 ID로 재호출하여 저장된 원격 ID를 조회한다. 제출 POST 수락 여부가 불명확하면 자동 재제출하지 않는다. 설정·코드가 달라진 구형 실행에는 새 `--run-id`가 필요하다. 수집기는 생성기 설치 없이 동작하도록 독립 전송 모듈을 보유하고, 생성기와 동일한 전송 계약을 회귀로 검사한다. [공식 Batch API 문서](https://ai.google.dev/gemini-api/docs/batch-api)는 검색 도구·구조화 출력과 비동기 처리를 설명한다. 모델/계정별 실제 이용 가능성은 이번 모의 검사로 보증하지 않는다.

# 기사 재서술 및 콘텐츠 권리 정책 · 2026-11-13

기사 전문은 그대로 생성기에 넘겨 근거를 대조한다. 대본은 기사 문장·문단의 복사나 동의어 치환 대신 핵심 내용을 새 구성과 문장으로 작성한다. “○○일보에 따르면” 같은 근거 있는 언론사 인용을 허용한다. 의견의 짧은 직접 인용은 기존 검증·귀속 규칙에 따른다.

새 대본과 자체 작성 사실 카드/요약은 `All rights reserved`, `© 틈(TEUM) 팀. All rights reserved.`로 표시한다. 권리 표시는 자체 작성 표현·편집에 한정하며 객관적 사실 자체와 제3자 기사·인용은 제외한다. 원문을 포함한 인계 Markdown의 전체 license는 unknown과 원기사별 조건을 유지하고 notes에서 팀 작성 부분의 권리를 구분한다. CC0를 새 출력에 부여하지 않는다. publish-license는 기존 대본의 메타데이터를 명시적으로 전환하는 오프라인 명령이며 실행 전후 사본을 남긴다. 이전 CC0 정책으로 진행 중인 트랜잭션은 자동 재개하지 않는다. 이번 코드 수정은 기존 수집 결과·대본을 소급 변경하지 않는다.

# 이슈 수집 에이전트

## 수집 → 입력함 한 번에 실행 (factory 연결용)

```bash
# 어느 작업 디렉터리에서든 실행 가능. 총 5편 = 시사 2 + 국내 시장 3.
bash <저장소 경로>/ai/issue_collector/collect_to_inbox.sh \
  --limit 5 --inbox <저장소 경로>/ai/script_agent/input

# issue_collector 디렉터리에서 바로 실행
./collect_to_inbox.sh --limit 5 --inbox /absolute/path/to/input
```

`--limit`은 두 분야를 합친 **최종 소재 상한**(기본 5, 1~40)이다. 시사는 `N // 2`, 시장은 `N - N // 2`편으로 수집 전부터 제한한다. 검증에 필요한 기사 조회 수/API 호출 수의 상한을 뜻하지 않는다. 한쪽 부족분을 다른 쪽으로 채우지 않으며, 요청 편수를 모두 확보했을 때만 해당 실행의 검증된 Markdown을 전달한다. 기존 `collect --count`는 여전히 **분야별** 개수이며 입력함 전달 없이 수집만 한다. 앞 단계의 결과가 다음 요청에 필요하므로 선정→추출→검증은 순차 진행하며 각 Gemini 단계는 배치 작업으로 제출한다. 실제 기사 HTTP 조회·Naver API 조회는 LLM 배치 대상이 아니다.

기본 대상일은 한국시간 전날, 기본 실행 ID는 날짜별 `inbox-daily-v1`이다. 같은 명령은 동일 계약의 기록을 재사용하며, 소비된 입력도 전달 영수증으로 중복 투입을 막는다. 진행은 stderr, 결과 JSON 한 개는 stdout으로 출력한다. `--dry-run`은 인증을 읽거나 API·파일 쓰기를 실행하지 않고 배분과 경로만 확인한다.

설치·인증 이름·`TEUM_COLLECT_CMD` 설정·종료 코드·중단 복구 계약은 [factory 연결 안내](tools/INTEGRATION.md)를 참조한다. 스크립트는 생성기나 factory를 실행하거나 예약하지 않는다.

한국시간 기준 주요 시사 이슈와 국내 시장 뉴스를 수집하고, 검증 사실·원기사 전문·선택적 의견 인용을 `script_agent`로 전달한다. 기본은 사실 전달이며 인용은 없어도 된다. 미국장은 현재 수집 범위에 포함하지 않는다.

## 수집과 원문 인계

RSS·공식 발표 목록·Naver 뉴스 검색으로 후보를 찾고, 같은 사건을 묶어 실제 기사 본문을 확보한다. 제목·검색 요약만으로 사실을 승인하지 않는다. 사건일·기관 귀속·수치·계획과 완료를 대조하고, 기본적으로 독립 원출처 두 곳과 검증 사실 세 개 이상을 요구한다. 의견은 사실 개수나 독립 출처 수를 늘리는 데 사용하지 않는다.

새 인계 v6은 YAML frontmatter와 본문을 담은 단일 `IC-*.md` 파일이다. 검증 사실, 사실별 근거, 승인된 의견 목록, 모든 수집 기사 본문과 출처·본문 SHA-256을 보존한다. 입력 전체를 CC0로 표시하지 않으며 원기사 이용 조건은 확인된 범위대로 기록한다. 원문이 상한을 넘으면 잘라서 전문으로 내보내지 않고 제외한다. 기본 기사 상한은 16,000자, 인계 본문 상한은 160,000자다.

수집 자체는 `script_agent`를 가져오지 않는다. 생성기는 수집기의 코드·캐시·사이드카 없이 Markdown 하나를 읽으며, 기사 블록의 개수·순서·중복 ID·해시와 근거 구간을 검증한다. 이슈는 C16, 국내 시장은 C08로 인계하며 stream도 별도로 보존한다.

## 선택적 의견 인용

승인된 의견은 기사 ID·언론사·실제 발언자·정확한 원문 구간·관련 사실·직접/간접 방식을 가진다. 사실만으로 충분하면 사용하지 않으며, 사용해도 최대 두 문장이다. 같은 기사에서 승인 구간을 포함하는 연속 원문을 근거로 사용할 수 있다. 인명은 유지하되 긴 직함은 줄일 수 있고, 같은 의견의 바로 이어지는 두 문장은 명확한 앞 문장 귀속을 공유한다.

생성기 근거 카드 ID는 의견도 `f1`, `f2` 형식을 쓴다. 수집기의 `O1` 등은 카드 `conditions`의 독립 원소로 연결한다. 직접 인용은 `quotation`, 간접 인용은 `opinion` claim이며, 의견을 확정 사실로 바꾸지 않는다. 입력 attribution은 인용 후보 매체 목록이다. 최종 attribution은 실제 대본의 검증 사실·의견 근거에 연결된 인용 언론사 이름만 기록하고, 인용이 없으면 빈 문자열로 둔다. URL·기사 제목·기자·기관을 추가하지 않는다. 원출처 전체는 별도 메타데이터에 남는다.

검증 사실 또는 승인 의견과 연결된 언론사 표기를 허용하고, 근거 없는 매체명만 수정한다. 발언 동사를 유한 목록으로 제한하지 않으며, 의미·추정 강도·주체는 원기사 대조 검토로 확인한다. 별도 검토에도 승인 의견의 인접 문장 귀속 위치를 전달한다. 문장 길이·분량·사실 조건 검사는 그대로 적용한다.

## 설치와 실행

Python 3.11 이상을 사용한다. 아래 명령은 `ai/issue_collector`에서 실행한다.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
bash tools/run.sh collect --date 2026-11-12 --streams issue market --count 3 --run-id daily-v1
```

키는 이 프로젝트 루트의 `.env` 또는 환경변수에서 로드한다. `gemini_api_key`, `openai_api_key`와 Naver 검색 자격증명을 사용하며 실제 키는 저장소에 넣지 않는다. Naver는 `X-NCP-APIGW-API-KEY-ID`·`X-NCP-APIGW-API-KEY`를 우선하고, 기존 `naver_client_id`·`naver_client_secret`도 지원한다. 자세한 수집·검색·모델 설정은 `config.toml`에 있다. `ISSUE_COLLECTOR_PYTHON`으로 실행 Python을 명시할 수 있다.

`--date`는 실행일이 아닌 사건 기준일이다. 생략하면 Asia/Seoul 기준 전날을 사용한다. 동일한 코드·설정·프롬프트의 중단 작업만 같은 run ID와 `--resume`으로 재개한다. 설정이 바뀌면 새 run ID를 사용한다.

생성기에서는 내보낸 파일을 직접 지정한다.

```bash
# ai/script_agent에서 실행
bash tools/run.sh validate-input /absolute/path/IC-20261112-issue-topic.md
bash tools/run.sh generate /absolute/path/IC-20261112-issue-topic.md --destination /absolute/path/new-output
```

`generate`는 원본을 이동하지 않으며 완료된 결과를 덮어쓰지 않는다. 큐 실행에서는 IC 입력 사본을 생성기의 `archive/<입력 SHA-256>/`에 보존한다. 과거 입력·검토 기록을 새 계약으로 소급 변환하지 않는다.

## 선택적 통합 명령

아래 명령은 같은 Python 환경에 두 프로젝트를 설치했을 때 사용한다.

```bash
.venv/bin/pip install -e ../script_agent
bash tools/run.sh demo --date 2026-11-12 --run-id daily-v1 --demo-id daily-v1 --streams issue market --count 3
bash tools/run.sh assess-use --demo-id daily-v1
```

`demo`는 실제 생성·검토·최대 두 차례 내부 수정을 수행한다. `assess-use`는 대본·근거 카드·수정 횟수를 변경하지 않고 별도 사실·표현 검토를 수행한다. 문장 코드 검사에 실패한 작업은 hold와 `facts_review=not_run` 및 구체 사유를 기록하고 나머지를 계속 검토한다. 원문·근거 무결성 오류는 중단한다. 중단 후에도 기존 검토 기록과 처리 권한 판단을 보존한다.

출력은 `output/<날짜>/runs/<run-id>/`, 통합 결과는 `demo/<demo-id>/`에 저장한다. 원문·검색·모델 요청/응답·검토 기록은 로컬 실행 자료이며 저장소에서 제외한다. `tools/refine_demo.py`는 저장된 대본에 명시적인 외부 피드백을 적용하는 보조 도구다. 서비스·타이머 파일은 경로와 실행 환경을 설정한 뒤 사용하는 예제이며 자동 설치하지 않는다.

## 검증과 상태

두 프로젝트를 설치한 개발 환경에서 `python -m pytest tests -q`로 회귀를 실행한다. 테스트는 자체 작성 자료와 모의 응답을 사용하며 실제 수집 결과나 API 키를 요구하지 않는다.

파일 생성 성공, 자체 검토, 별도 사실·표현 검토, 수집·저장·외부 모델 전송 권한, 사람 승인, TTS 검증은 서로 다른 상태다. 일반 기사에 인용이 있다는 사실만으로 사용 권한이 확보되었다고 판단하지 않는다. 자동 검토 통과를 서비스 게시 승인으로 해석하지 않는다. 이전 v4 사실 전용 인계와 v5 원문 인계의 호환 처리는 유지한다.
