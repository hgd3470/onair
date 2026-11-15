## 로컬 모델 설정 · 2026-11-13

현재 `config.toml`의 분석·생성·수정 모델은 `gemini / gemini-4-hydrogen / medium`, `[pipeline] mode = "batch"`다. 검토·재검토는 `openai / gpt-6-luna / medium`, 기본 프롬프트 v27, 내부·외부 수정 예산 각각 최대 2회를 유지한다. Gemini 배치에는 `gemini_api_key`를 사용하고 서비스 계정 설정이 함께 있어도 API 키 인증을 유지한다. 아래 과거 모델명과 Vertex 실행은 당시 기록이다.

`bash tools/run.sh run --limit 5`는 기본 배치 큐로 실행된다. 단일 `generate`, 외부 피드백 `revise`, 실험·demo 및 preflight의 Gemini 호출도 기본 모드를 따라 배치 요청을 사용한다. `generate`/`revise`의 의존적인 각 단계는 단일 요청을 담은 배치이며, 큐 `run`/`watch`는 기존 단계별 다건 배치를 유지한다. OpenAI 검토는 실시간이다. `run`/`watch`/`generate`/`revise`의 `--mode standard`로 명시적으로 전환할 수 있다.

`[batch] poll_interval_sec=15`, `wait_timeout_sec=86400`은 원격 작업 조회 주기와 한 작업의 대기 상한이다. 원격 ID와 완료 응답을 보존하므로 조회 실패·중단 후 동일 요청을 재개할 때 재제출하지 않는다. 제출 수락 여부가 불명확하면 `batch_submission_unknown`으로 멈춘다. 외부 수정은 `external-batch-feedback-N`의 고정 로그를 사용하며 같은 버전·같은 피드백만 재개한다. 완료 전에는 버전·수정 횟수를 증가시키지 않는다. preflight도 실제 배치 작업을 제출해 기다릴 수 있으며 완료된 동일 검사 응답은 재사용한다. 구형 로그·변경된 설정/프롬프트에는 새 목적지를 사용한다. 실제 Gemini 배치·TTS 실행은 이번 변경 검증에 포함하지 않았다.

## 기사 재구성·언론사 인용·팀 저작권 · 2026-11-13

기본 프롬프트 v27은 기사 전문을 검증 자료로 보존하고, 대본은 검증 내용을 독립적인 문장과 구성으로 새로 작성하도록 한다. 근거 기사·사실 카드·claims가 연결된 “○○일보에 따르면” 인용은 허용한다. 의견은 기존 승인 목록·귀속·짧은 직접/간접 인용 규칙을 따른다. 아래 과거의 일괄 매체명 금지보다 이 계약이 우선한다.

이슈 수집기 대본은 실시간·배치·외부 수정 모두 최상위 license=All rights reserved, rights_notes의 © 틈(TEUM) 팀 표시를 사용한다. 원기사·입력 라이선스·원문과 검토/게시 상태는 유지한다. 독립 사실 자료는 같은 팀 권리 유보 조건을 지원하고 과거 CC0 입력은 재현용으로 읽는다. 과거 CC0 공개 선언은 새 출력에 적용하지 않는다. v5/v6 모두 실제 인용한 매체를 attribution에 재계산하며, 명시적 publish-license 실행에서도 유지한다. 변경된 프롬프트에는 새 실행 목적지를 사용한다.

# S2-05 대본 자동화 API 프로토타입

## 원문 인계 v6의 선택적 언론 의견 인용 (v26)

기본은 사실만 전달하며 인용 0개는 결함이 아니다. 인명은 유지하며 긴 직함은 줄일 수 있고 동일 의견의 바로 이어지는 두 문장은 귀속을 공유한다. 발언 동사 목록 대신 의미 검토로 의견의 확실성을 확인한다. 근거가 연결된 인용은 다른 검사에 실패해도 일반 매체명 삭제 지시와 중복되지 않는다. 실제 사용한 매체만 attribution에 남긴다. 내용 있는 귀속 전망을 단순 출처 안내로 거부하거나 앞선 제안을 문체 취향으로 번복하지 않는다.

`issue_collector`의 새 v6 Markdown은 검증 사실과 별도 `의견 인용 목록`을 제공한다. 기본 프롬프트 v27은 v5/v6의 근거 있는 사실 보도 귀속을 허용하며, v6에는 선택적 의견 인용 정책도 적용한다. 승인 의견은 선택적으로 최대 두 문장, 언론사·실제 발언 주체·불확실성을 보존해 전달한다. 직접 인용은 원문 그대로 `quotation`, 간접 인용은 짧은 재서술로 `opinion` claim을 사용한다. 두 방식 모두 `kind=opinion` 카드의 quote(승인 구간 전체 또는 이를 포함한 동일 기사 연속 구간)와 연결하고 `conditions`에 독립 O번호 문자열을 넣는다.

최상위 `attribution`은 실제 연결된 인용 언론사 이름만 쉼표로 구분한다. URL·기사 제목·기관·기자는 제외하며 인용이 없으면 빈 문자열이다. 입력 메타데이터와 원출처 전체는 보존한다. 실시간·배치·외부 수정에서 자동 재계산하고 원문 변조·미승인 의견·귀속 누락·의견의 사실 분류를 검증한다. 의미·관련성·불확실성의 정확성은 모델의 원기사 대조 검토도 필요하다.

선택적 통합 원기사 검토는 `UseAssessment.policy_version=news-attribution-v2`, `basis=facts_with_optional_attributed_opinions`를 사용한다. 실제 의견이 있으면 `facts_only=false`와 `attributed_opinions=true`를 요구하고 통과 시 `rights_status=attributed_news_reviewed_not_legal_approval`로 구별한다. 의견 없는 결과는 기존 사실 검토 상태를 사용한다. 기사 권리·처리 허가·사람 승인·TTS 상태와는 별개다. 기존 v23 프롬프트와 출력은 보존하며 새 목적지에서 실행한다.


## 원문 Markdown 독립 인계 v5 / 프롬프트 v23 · 2026-11-12

`issue_collector`가 내보내는 `IC-*.md`를 변환 없이 읽는다. YAML frontmatter에는 `source_kind=user_summary`, 분류·목표 길이·`original_sources`가 있고, 본문에는 검증 사실·근거 매핑·`## 수집 원문` 아래 기사 전문이 있다. 생성기는 수집기의 Python 코드나 캐시를 불러오지 않는다. `.meta.json`은 필요하지 않다.

```bash
bash tools/run.sh validate-input /absolute/path/IC-20261111-issue-topic.md
bash tools/run.sh generate /absolute/path/IC-20261111-issue-topic.md --destination /absolute/path/new-output
```

`generate`는 단일 파일에 설정된 모드의 생성·검토·수정 파이프라인을 실행한다. 원본은 그대로 두며 `sources[0].text_path`는 전달받은 파일을 가리킨다. 완료 JSON이 있으면 덮어쓰지 않고 `output_exists_use_new_destination`으로 종료한다. 미완료 로그는 기존 설정·프롬프트 일치 검사를 거쳐 재개한다. `--destination` 생략 시 기존 `output/<배치코드>/` 분류를 사용한다. `IC-`는 `_unbatched`다.

큐 방식도 지원한다. `.md`를 `input/`에 복사한 뒤 `run --mode standard` 또는 `run --mode batch`를 실행한다. 새 `IC-` 입력의 완료 사본은 생성기 자체 `archive/<입력 SHA-256>/`에 보관하므로 다른 수집기 폴더를 생성하지 않는다. 기존 `source_collector` 입력의 저장 경로는 유지한다.

분석·생성·검토·수정 요청의 `source_text`, 최종 JSON의 `sources[0].text`에는 원기사 전문을 포함한 입력 본문 전체를 저장한다. `sources[0].sha256`은 이 전체 본문 해시다. 원출처 목록은 `input_metadata.original_sources` 및 `sources[0].original_sources`에 보존한다. 외부 피드백도 JSON에 내장된 원문으로 수정·재검토하며 원본 파일 재조회가 필요하지 않다. v5 입력 인계 형식은 유지하며 선택적 인용의 출력 상태는 위 v6 계약을 따른다.

v5 원문 블록은 개수·순서·중복 기사 ID·본문 SHA-256을 검사한다. 사실 카드 `quote`가 검증 요약에만 있고 기사 본문에 없으면 `fact_quote_not_in_original_article`로 거부한다. v23 프롬프트는 원문과 검증 사실을 직접 대조하며, 기사에만 있는 미검증 사안을 추가하지 않게 한다. 기존 v2~v4 입력과 비뉴스 입력은 계속 지원한다. v22는 보존한다.

기본 입력 상한은 60,000→160,000자로 확대했다. `--max-source-chars` 명시값과 오류 계약은 유지하며 원문을 자동 절단하지 않는다. 입력·원기사 이용 조건은 미확인 상태를 보존하고 원문 전체에 CC0를 부여하지 않는다. 모델·effort·내부/외부 수정 예산은 유지한다. 기존 산출물은 자동 변경하지 않으며 새 설정·프롬프트는 새 실행 목적지에서 사용한다.

## 저장 경로 · 2026-11-10

- 원본: `../source_collector/output/<YYMMDD-HHMM>/<source_id>.md`
- 대본: `output/<YYMMDD-HHMM>/S2-<hash>.json` (소스 코드에 배치 접두사가 없으면 `_unbatched/`)
- 실시간·배치·외부 수정 로그: `logs/<content_id>/`; 공유 배치 제출 기록: `logs/batch-jobs/`
- 과거 output 내부 실행 부속 자료: `logs/legacy-output/`
- 대기·진행·실패: `input/`, `processing/`, `failed/`; 완료 작업 상태: `state/completed/`
- `submission/`, `submission_2/`, `only_test/`는 별도 대본 묶음이며 이번 정리에서 보존했다.

원문 파일의 자동 archive 및 output/sources 백업은 제거했다. 출력 JSON의 검증용 원문과 API 요청·응답은 유지한다. 성공한 입력은 수집기 원본과 대조한 뒤 제거하며, 원본이 없으면 먼저 등록한다. 실패·처리 중 입력은 재시도를 위해 유지한다. export는 하위 배치 폴더까지 읽고 원문 파일을 추가 복사하지 않는다. 과거 scratch 실행 스크립트는 당시 경로를 전제로 하므로 현재 운영 명령은 이 구조를 따른다. 기존 제출본의 text_path는 변경하지 않았다.


Gemini의 원문 분석·초안 생성 → GPT 검토 → 필요 시 Gemini 수정 → GPT 재검토 → S2-06 전달용 JSON.
S2-06의 독립 검수·공개 승인·TTS·서버 적재는 담당 범위 밖이다.

분석·초안·수정은 **gemini-4-hydrogen / medium**, 검토·재검토는 **gpt-6-luna / medium**이다. 수정은 `config.toml`의 `models.generator` 설정을 초안 생성과 공유한다. 오류가 나도 모델을 자동 대체하지 않는다. `config.toml`에서 역할별 설정을 바꿀 수 있다. LangChain의 모델 교체 목적을 작은 `Provider` 인터페이스로 구현했고 공식 HTTP API를 사용한다. 추론 라이브러리/GPU/Seraph가 필요하지 않다.

## 저장소 배치

`ai/script_agent/`는 원문에서 대본을 만드는 독립 Poetry 프로젝트다.
기존 `ai/content/`의 TTS·검수·게시 파이프라인과 환경을 분리한다.
생성 JSON은 독립 검수와 형식 변환 후 다음 단계로 전달하며 자동 등록하지 않는다.

코드·설정·프롬프트·스키마·테스트·자체 작성 예제만 버전 관리한다.
실험 보고서, 모델 응답, 실제 생성 대본, 외부 수집 원문과 인계 샘플은 포함하지 않는다.
기본 프롬프트는 `v22`다. `v1`~`v21`은 과거 실행 재현용으로 보존한다. v20~v22는 뉴스의 기관 귀속·예정/완료·낭독 문체와 독립 사실 소스 인계 경계를 보강한다. [v22 안내](prompts/v22/README.md)와 아래 버전별 계약을 참고한다. v19는 단어 누락·문법 교정·비연속 문단 결합에 따른 인용 실패를 줄이도록 분석 지시를 보강한다. 원문 일치 검사는 유지한다. [v19 안내](prompts/v19/README.md)를 참고한다. v18은 정제 피드백의 원문 표현·사건 조합 재사용, 사실 범위·조건·인과, 청취 난도와 결말 반복을 유형별로 반영한다. [v18 안내](prompts/v18/README.md)의 검증 범위와 한계를 참고한다. 실제 43편 실행은 대본별 editorial_brief도 사용하므로 일반 프롬프트 단독 효과로 해석하지 않는다. 이관 후 수정 단계의 담당 모델을 GPT에서 Gemini로 변경했다. 이 변경만으로 품질 향상을 확정하지 않는다.

`v7`에서 도입하고 `v12`에서도 유지하는 생성·검토·수정 규칙은 다음과 같다. 코드 검사도 실패 항목을 수정 피드백에 넣으며, 수정 한도 뒤에도 남으면 `hold`다.

- 낭독문 숫자·영문은 한글 읽기로 쓴다. `20메가와트 → 이십 메가와트`, `9급 → 구 급`, `AI → 에이아이`. 수치·단위·날짜의 의미는 모델이 원문과 대조하며 코드는 잔존 표기를 검사한다.
- 문장 하나는 **공백·문장부호 포함 NFC 기준 40자 이하**다. 긴 문장은 조건·주체를 유지한 완결 문장으로 나누고 `claims`를 다시 매긴다.
- 사실해설형도 원문의 문장·독특한 표현을 그대로 옮기지 않는다. 코드의 표면 유사도 경고와 모델의 표현·의미 대조를 함께 사용한다.
- 의료진 등 개인의 말을 보편적 의학 사실로 확대하지 않고 발화자·대상·상황을 보존한다.

원문 스냅샷과 사실 카드의 `quote`는 그대로 보존한다. 원문낭독형은 원문 보존과 40자·한글 읽기가 충돌하면 `needs_evidence/hold`로 남겨 판본·낭독 범위를 확인한다.

## 청취 피드백 반영 (v8 → v12)

사람 피드백의 용어 연결·예시·장면 도입·마무리·성경 출처 안내를 분석→생성→검토→수정 전체에 반영했다. 모델과 호출 단계는 유지하며 추가 모델 호출은 없다.

- 분석은 기존 `question/summary/details/outline`에 중심 질문, 핵심 답, 근거 있는 쉬운 설명·예시·장면, 실제 마무리 요점을 설계한다.
- 생성은 설명 없는 전문용어와 수치 나열을 줄이고, 원문 예시를 개념에 연결한다. 끝은 도입 질문의 답이나 적용 범위로 닫는다.
- 검토는 사실성뿐 아니라 처음 듣는 사람의 이해 가능성을 확인한다. 의미 연결이나 마무리 누락도 구체적인 `issues`와 `revise`로 반환한다.
- 수정은 상투적인 교훈 한 줄을 붙이는 대신 질문·전개·결말을 함께 맞춘다. 근거 없는 안전 보장·행동 처방은 만들지 않는다.
- 성경 본문은 첫 한두 문장에 성경과 확인된 책·장/절을 안내한다. 개인 신앙 경험을 성경으로 오인하지 않으며, 원문낭독형은 원문 보존·해설 결말 생략을 유지한다.

변경 근거와 항목별 수용 기준은 [v12 안내](prompts/v12/README.md)를 참고한다. 기존 출력은 자동으로 고쳐지지 않는다. 새 제작은 별도 목적지에 기록하고 기존 대본의 수정 횟수를 초기화하지 않는다. 기존 배치를 이어갈 때에는 당시 프롬프트·설정과 로그가 일치해야 한다. 일반 CLI의 `--prompt-version v7`로 과거 버전을 명시할 수 있다. `review_batch.py`는 저장된 선택·설정과 현재 설정이 다르면 새 목적지를 요구한다.

검토 화면의 코멘트 JSON은 피드백 맥락 자료이며 `revise.jsonl`과 다른 계약이다. 사람의 `keep`은 자체 검토 통과나 공개 승인으로 치환하지 않는다.

### 편집 요구사항을 대본별로 추적하기 (v11)

카테고리별 새 제작에는 `tools/review_batch.py --editorial-briefs /path/to/briefs.json`을 사용할 수 있다. JSON은 `{"C20": ["도입의 질문을 마지막 한두 문장에서 회수한다."]}`처럼 카테고리→전체 원고의 요구사항 배열이다. 검토 화면의 원본 코멘트 JSON이나 문장 번호별 revise.jsonl을 이 파일로 자동 해석하지 않는다. 이전 문장 번호를 새 원고에 그대로 적용하지 않는다.

요구사항은 분석·생성·수정·재검토 모두에 전달되며 출력의 `editorial_brief`에 보존된다. 검토 모델은 항목 번호·충족 여부·실제 문장 근거를 `self_review[].editorial_checks`로 남긴다. 항목 누락·중복·불일치나 미충족은 코드가 수정/hold로 연결한다. 체크 결과는 모델의 진단이지 사람 승인으로 취급하지 않는다. 같은 배치의 요구사항을 바꾸면 새 목적지를 사용해야 한다.

수정 모델은 사실 카드 변경이 없을 때 `material=null`을 반환할 수 있다. 코드는 기존 카드 전체를 그대로 사용한다. 변경할 때는 전체 카드를 반환하고 사실 ID·원문 근거·유형 보존 검사를 거친다. 원문 인용에서 강조 기호·공백만 달라졌고 대응 구간이 하나이면 원문의 정확한 연속 구간을 복원한다. 원본 모델 응답과 복원 내역은 로그에 따로 남기며 숫자·단어·부정어·절 번호를 바꾸거나 모호한 구간을 맞추지 않는다.

OpenAI 응답 미완료·거부는 허용된 응답 상태·종료 사유·토큰 수·거부 여부만 기록한다. 거부문·응답 본문은 저장하지 않으며 자동 재시도하지 않는다. 과거 종료 사유 없는 오류를 토큰 부족이나 거부로 추정하지 않는다. Gemini 응답 미완료는 허용된 종료 사유·토큰 수만 오류 기록에 남긴다. C11 실제 생성에서 16,000 한도 중 내부 추론 15,358토큰 사용 후 MAX_TOKENS가 확인되어 생성 모델의 출력 상한을 32,000으로 올렸다. 모델과 medium 설정은 유지했다. 오류 응답 본문·비밀키는 기록하지 않으며 차단·연결 오류를 자동 재시도하지 않는다. C07의 과거 미완료 원인은 당시 종료 사유가 없어 확정하지 않는다.

### 실제 피드백 재검증 · 2026-11-08

수정 의견 7편의 선택된 최종 출력이 모두 자체 검토를 통과했다. C11·C20·C21은 v11 생성·수정 결과, C07은 v11 생성 이후 명시적 직접 편집과 v12 재검토 결과다. 이미 통과한 C08·C13·C18의 v9 결과는 보존했다. C07은 용어 정의와 연구 범위를 분리하고 근거 카드 연결·농도 단위를 고쳤다. 자동 수정 한도 2회를 늘리지 않았으며, 이후 코딩 에이전트의 직접 편집은 `1.0.2+editorial.4`와 별도 감사 기록으로 구분했다. 새 기본 v12에는 이 과정에서 확인한 범위·근거표·분량 규칙을 반영했으나 전체 7편을 v12로 자동 재생성해 통과한 결과는 아니다.

최종 비교는 `scratch/feedback-resolution-20261108/comparison.md`, 검증은 `verification.json`, 대본·원문 인계는 `handoff/`, 로컬 16편 검토 화면은 `review/index.html`이다. 원문과 기존 배치·실패 기록은 보존했다. 회귀 검사 149개 통과. TTS·사용자 재검토·공개 승인은 미실행이다.

## 설치

Python 3.11 이상, Poetry 2.x가 필요하다. 프로젝트별 가상환경과 Poetry 도구는 Git에 포함하지 않는다.

```bash
cd /path/to/hyundai_pleos_app/ai/script_agent
# Poetry가 이미 있으면 다음 2줄은 생략
python3 -m venv .tools/poetry
.tools/poetry/bin/pip install 'poetry>=2.2,<3'
POETRY_VIRTUALENVS_IN_PROJECT=true .tools/poetry/bin/poetry sync
```

다른 환경에서는 같은 `poetry.lock`으로 설치한다. 이후 명령의 `.tools/poetry/bin/poetry`는 PATH의 `poetry`로 바꿔도 된다.

Linux/macOS에서는 위 설치 과정을 `bash tools/bootstrap.sh`로 실행할 수 있다. 사용자가 이 폴더에 `.env`를 배치한 뒤 `bash tools/run.sh run`으로 입력 폴더를 처리한다. Windows에서는 Python/Poetry 명령을 직접 사용한다. 배포 ZIP은 코드·문서·설정·프롬프트·스키마·테스트 예제만 포함하며 `.env`, 설치된 가상환경과 실행 결과는 포함하지 않는다.

키는 **`ai/script_agent/.env`**에 사용자가 직접 배치한다. 형식은 다음과 같으며 실제 값은 Git에 커밋하지 않는다.

```dotenv
openai_api_key=YOUR_OPENAI_KEY
gemini_api_key=YOUR_GEMINI_KEY
```

애플리케이션은 기본적으로 프로젝트 루트의 `.env`만 읽고 상위 폴더를 탐색하지 않는다.
기존 프로세스 환경변수(소문자, 대문자 순)가 파일 값보다 우선한다.
다른 파일이 필요할 때만 `--env-file /absolute/path/to/.env`로 재정의한다.
키 값·접두사·길이·HTTP 헤더·원문 오류 응답은 기록하지 않는다.

```bash
# 프로젝트 폴더에서 실행. preflight도 실제 모델 API를 호출한다.
bash tools/run.sh preflight

# 실시간 순차 실행 (standard 모드)
bash tools/run.sh run --mode standard

# Gemini Batch API 비동기 고병렬 실행 (batch 모드: 50% 비용 할인, GPT 실시간 병렬 검토)
bash tools/run.sh run --mode batch

# 모드 인자 생략 시 터미널에서 대화형으로 standard/batch 선택 (비대화형 시 config.toml의 mode 기본값 적용)
bash tools/run.sh run

# 설치 후 다른 폴더에서도 절대 경로로 호출할 수 있다.
# run.sh가 ai/script_agent를 루트로 정해 그 안의 .env를 사용한다.
bash /path/to/hyundai_pleos_app/ai/script_agent/tools/run.sh run --mode batch
```

저장소 기본 설정은 `provider = "gemini"`, `prompt_version = "v22"`다. Gemini API 키 방식은 `gemini_api_key`로 인증한다. 선택 기능인 Vertex AI 인증과 관련 의존성은 유지한다. `google_application_credentials`가 있으면 provider가 `gemini`여도 서비스 계정 인증 경로를 사용하므로, Gemini API 키 실행 환경에는 해당 항목을 지정하지 않는다. 인증 파일·입출력·로그·제출 묶음은 Git에서 제외한다.

### 실행 모드 (standard vs batch)

1. **standard 모드**: 입력 폴더의 소스를 1건씩 순차적으로 Gemini API(실시간) 및 GPT API로 처리합니다. 단건 디버깅이나 소량 실행에 적합합니다.
2. **batch 모드**: Gemini Batch API를 활용하여 `input/`의 모든 소스를 비동기 일괄 제출합니다.
   - **50% 비용 절감**: Gemini 정규 비용 대비 50% 할인 적용.
   - **병목 해소 & 고병렬 처리**: 분석(Analyze) 및 생성(Generate) 단계를 최대 `batch_size`(기본 100건) 단위 청크로 Gemini Batch API에 일괄 투입하여 병렬로 처리합니다.
   - **GPT 실시간 검토 최적화**: 결과 회수 후 실시간으로 돌아가는 GPT 검토는 2 OCPU / 12GB RAM 사양에 최적화된 스레드 풀(기본 6 워커)로 병렬 처리하여 대기 시간을 최소화합니다.
   - **단일 설정 파일(`config.toml`) 관리**: 모델명(`gemini-4-hydrogen`, `gpt-6-luna`), 추론 강도(`effort`), 출력 토큰 등 모델 세팅은 `config.toml`의 `[models.generator]`와 `[models.reviewer]`에서 일괄 관리되므로 한 파일만 수정하면 두 모드에 동시 적용됩니다.

CLI를 직접 실행할 때는 이 폴더에서 실행하거나 `--root /path/to/ai/script_agent`를 지정한다.
`input/`은 저장소에 빈 폴더로 제공하며, `output/`, `logs/`, `state/`, `processing/`, `failed/`,
`evidence/`, `experiments/` 등은 실행 시 로컬에 생성되고 Git에서 제외된다.

## 입력

수집기의 긴 원문은 `script-agent --max-source-chars 256000 validate-input <파일>`로 검사한다. 제작에도 같은 전역 옵션을 `run`/`watch` 앞에 지정한다. 기본 config의 입력 한도는 유지하며, 명시 옵션은 해당 실행에만 적용한다(1~256,000자). 입력 설정이 달라진 기존 제작 체크포인트는 새 목적지를 사용한다. 원문을 잘라 기존 한도에 맞추지 않는다.

`input/` 최상위에 UTF-8 `.txt`/`.md` **한 파일당 한 소스**를 넣는다. 작성 중인 파일은 `.part`로 저장한 후 이름을 바꾸는 방식을 권장한다. 심볼릭 링크·빈 원문·과대 입력은 거부한다. `.env`와 다른 확장자는 소스로 처리하지 않는다.

그냥 텍스트만 넣어도 분석·생성한다. 이 경우 출처·이용 조건은 `unknown/null`이며 자동 허용하지 않고 인계 상태는 `hold`다. 외부 수집 담당자가 확인한 출처와 조건을 YAML front matter 또는 같은 이름의 `.meta.json`에 넣을 수 있다. 둘을 동시에 사용하지 않는다.

```yaml
---
source_id: story-001
source_url: https://example.org/source
category: C04
production_type: 창작·각색형
target_sec: 90
author: 작성자
license:
  kind: CC BY-SA 4.0
  version: '4.0'
  commercial: true
  modify: true
  share_alike: true
  url: https://creativecommons.org/licenses/by-sa/4.0/
  evidence: 실제 수집 시 확인한 이용 조건 위치/기록
attribution: '원문: 작성자 / 원문 링크 / CC BY-SA 4.0 / 번역·각색함'
info_as_of: '2026-11-08'
---
여기에 수집 당시 원문 텍스트를 넣습니다.
```

예시 라이선스를 임의로 실제 소스에 적용하지 않는다. `commercial/modify/share_alike`의 `null`은 미확인이다. 원문낭독형은 `edition`, 필요시 원문 안에 존재하는 `reading_text` 구간을 지정한다. 판본이나 권리 근거가 없으면 보류한다. 샘플은 `examples/sources/`에 있다.

명시된 CC 4.0 식별자/공식 조건 URL이 있으면 `kind`, `version`, `commercial`, `modify`, `share_alike`, `url` 중 비어 있는 항목을 표준 조건으로 보완한다. 지원 프로필은 BY/BY-SA/BY-NC/BY-ND/BY-NC-SA/BY-NC-ND다. 저자와 출처 URL이 있으면 출처 표시 초안도 구성한다. **해당 원문에 조건이 적용된다는 `evidence`는 수집 담당자가 제공해야 한다.** 원문이 공개되어 있거나 사용자 제공 자료라는 이유로 허용값을 만들지 않는다. 알 수 없는 값은 유지하고, 누락·충돌·자동 보완 내역을 출력의 `rights_notes`에 남긴다. `validate-input`에서도 API 호출 없이 이 내역을 확인할 수 있다.

사용자 제공 요약문은 `source_kind: user_summary`로 표시하고 `original_sources`에 원래 기사/자료의 `title`, `source_url`, `author`, `license`, `attribution`을 각각 넣는다. 원출처가 없거나 그 조건이 미확인이면 보류한다. 이 목록은 `sources[].original_sources`에도 전달되며 URL만으로 원문을 읽었다고 간주하지 않는다. 입력 예시는 `examples/user-summary.meta.json`을 참고한다. 직접 작성·이용 허락 등 다른 조건은 확인한 네 핵심 필드와 근거를 명시한다.

## 처리와 복구

- `input → processing → output/<배치코드>` 순서로 처리한다. 완료 원본은 `../source_collector/output/<배치코드>/accepted/` 한 곳에 두고, 임시 입력은 제거하며 작업 상태만 `state/completed/`에 보존한다.
- 오류는 `failed/<job_id>/`에 원문·메타데이터·비밀값 없는 오류 코드를 남긴다. 정상 완료한 대본이 없는 원문은 성공 처리하지 않는다.
- OS 파일 잠금으로 같은 작업 폴더의 동시 실행을 막는다. 내용+메타데이터 SHA-256으로 중복 API 호출을 피한다.
- 단계별 요청·응답·모델·프롬프트 해시·사용량을 저장한다. 중단 후 동일 입력/설정으로 실행하면 완료된 단계부터 재사용한다. 응답 수신 직전 연결이 끊긴 경우 실제 과금 여부는 알 수 없으므로 자동 즉시 재호출하지 않는다.
- 401/403/429는 계정 상태 문제로 보고 배치를 멈춘다. 실패를 성공/품질 평가로 세지 않는다.

```bash
# 한 번 처리
.tools/poetry/bin/poetry run script-agent run --limit 5
# 입력 감시; Ctrl-C 후 처리 중 자료 보존
.tools/poetry/bin/poetry run script-agent watch --interval 15
# 실패 원인을 해결한 뒤 명시적 재시도
.tools/poetry/bin/poetry run script-agent retry JOB_ID
.tools/poetry/bin/poetry run script-agent run
# 호출 없는 입력 검사
.tools/poetry/bin/poetry run script-agent validate-input examples/sources/adaptation.md
```

## 출력과 외부 검수 연결

`output/<배치코드>/S2-<hash>.json` 한 개가 대본 한 편이다. `sources[].text`에 실행 당시 텍스트를 포함하므로 JSON만 받아도 대조할 수 있다. `text_path`는 JSON 폴더 기준 수집기 원본의 상대 경로다. 실행·export는 별도 `sources/` 복사본을 만들지 않는다. `schema/handoff.schema.json`이 출력 계약이다.

`script`는 문장 배열이며 **1부터 시작하는 배열 위치가 문장 번호**다. `claims`는 문장별 사실 ID를 연결한다. `material.facts[].quote`는 원문 부분 문자열 검사 대상이며 카드의 주장이 사실로 외부 검증됐다는 뜻은 아니다. 사실·원문 서술·의견·창작·인용을 구분한다.

`license`, `attribution`, `source_url`, `script`라는 필드명은 현재 `/contents`와 맞췄다. 현재 서버의 `script`는 문자열이고 이 인계 계약은 배열이므로 서버 적재 담당자는 `"\n".join(script)`로 변환해야 한다. 여기서 기존 서버나 DB를 수정하지 않는다. 자세한 계약은 `SPEC.md`를 참조한다.

`ready_for_downstream_review`는 자체 검토 통과와 이용조건 메타데이터 존재를 뜻하며 공개 승인/법적 권리 보증이 아니다. `hold`도 대본 결과는 보존하되 미해결 문제를 해결한 뒤 넘긴다. `fixture=true`는 자체 작성 연결 시험용 원문이며 실제 사례나 외부 검증 자료로 사용하면 안 된다.

외부 검수 피드백은 아래 배열을 그대로 받는다. 생성기 내부 수정은 `generator.revision`, 외부 피드백 수정은 `generator.feedback_revision`으로 각각 최대 2회 관리한다(`max_revisions`를 각 한도에 적용). 새 대본은 실시간·배치 모드 모두 `feedback_revision=0`으로 저장하며 기존 JSON에 키가 없으면 0으로 읽는다. 외부 피드백 한 회차의 결과를 저장할 때 `feedback_revision`만 정확히 1 증가한다. 회차 안에서 자체 검토에 따른 추가 수정이 있어도 외부 회차는 더 증가하지 않는다. 각 외부 회차의 수정 루프는 0부터 시작해 `max_revisions`회(최초 피드백 반영 포함)까지만 수정하며, 실제 회차 내 수정 횟수는 `self_review[].feedback_round_revision`과 단계별 로그에 기록한다. 실패해 결과를 저장하지 못한 회차는 카운터와 기존 출력을 바꾸지 않는다. `version=1.0.N`의 N은 두 카운터의 합으로 증가한다. 피드백의 문장 번호 0은 대본 전체 범위다. 한도를 다 쓰면 별도 사람 판단/새 제작 결정이 필요하다.

```json
[{"문장 번호": 1, "문제": "고민을 실행으로 단정", "이유": "원문은 고민만 함", "수정 방향": "실행과 고려를 구분"}]
```

영문 `sentence/problem/reason/direction` 배열도 받는다. `feature/refine-v1.0`의 `revise.jsonl` (`script_id`, `round`, `decision`, `items`)은 대상 출력의 `content_id`와 같은 한 행을 선택한다. `sent/fix` 별칭도 지원하며 중복 대상·키 값 충돌·범위 밖 문장 번호는 거부한다. `decision=revise`만 자동 수정하며 `human_review`는 사람 확인 대상으로 반환한다. 외부 `round`로 수정 예산을 초기화하지 않는다. JSONL에는 원고 버전이 없으므로 `--expected-version`을 필수로 받는다. 검수자가 실제 검토한 버전을 사용해야 한다.

전체 지적은 내부적으로 `sentence=0`으로 처리한다. `scope=script`의 번호가 `null` 또는 생략이면 0으로 변환하고, scope 없는 이전 형식은 명시적인 `null`만 0으로 변환한다. `scope=sentence`는 양의 정수 번호가 필수다. 음수·문자열·소수·불리언·범위 밖 번호, scope/번호 모순과 별칭 충돌은 오류로 남긴다. CLI 수정 실행의 변환 내역은 `logs/<content_id>/external-*/feedback-normalizations.json`에 기록하고 입력 피드백 파일은 바꾸지 않는다.

모든 프롬프트 버전의 수정 단계에 `prompts/feedback-scope.txt`를 공통으로 덧붙인다. 전체 지적도 필요한 부분만 고치며, 분량 부족을 새 사실이나 반복으로 채우지 않는다. 실제 결합 프롬프트는 호출 로그와 해시에 반영된다.

```bash
.tools/poetry/bin/poetry run script-agent revise output/S2-ID.json examples/feedback.ko.json --expected-version 1.0.0
# 검수기 배치 출력에서 이 대본의 행만 선택
.tools/poetry/bin/poetry run script-agent revise output/S2-ID.json /path/to/revise.jsonl --expected-version 1.0.0
# 새 폴더로 전달 JSON+원문+manifest 묶음 내보내기. 기본은 hold 제외.
.tools/poetry/bin/poetry run script-agent export /path/to/new-handoff
```

## 검증과 배포 묶음

```bash
# 실제 API를 호출하지 않는 회귀 검사
.tools/poetry/bin/poetry run pytest -q
.tools/poetry/bin/poetry check --lock
# 코드·설정·문서·테스트 예제만 묶는다. dist/는 Git 제외.
.tools/poetry/bin/poetry run python tools/package_release.py
```

`tests/`는 모의 Provider와 자체 작성 예제를 사용한 파일·제어 흐름 검사다.
사람 청취·독립 검수·TTS 품질을 보증하지 않는다.
검사 범위는 모델 역할 분리, 문장 길이·한글 읽기, 원문 보존, 이용조건 보완·충돌, 외부 JSONL 피드백, 검토 HTML과 배포 파일 경계다. 이 테스트는 실제 모델 API를 호출하지 않는다. 외부 검수기와의 계약 확인 기준은 `SPEC.md`에 기록했다.

과거 실험용 `experiment` 및 `python -m script_agent.reviewer_experiment` 코드는 보존했다.
실제 API를 호출하며 결과는 Git 제외 경로에 생성한다.
`experiment`의 기존 마감(2026-11-08 09:00 KST)과 최대 30회 제한은 유지했다.
새로운 실험은 설정과 별도 작업 디렉터리를 명시적으로 준비해야 한다.
일반 `run`/`watch`에는 이 일회성 실험 마감이 적용되지 않는다.

## 근거

- S2-05 스프린트
- 여섯 제작 유형과 예시: 최신 각색 안내는 메타데이터에 표시, 본문은 사건으로 시작한다.
- 사용자 첨부 2026-11-07 21:28 KST 회의록과 1주차 템플릿. 회의록 속 Gemma/기존 논의보다 이번 사용자의 Gemini/GPT API 지정이 우선한다.
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [Gemini 텍스트 생성](https://ai.google.dev/gemini-api/docs/generate-content/text-generation), [Thinking](https://ai.google.dev/gemini-api/docs/generate-content/thinking)

외부 원문과 실제 실행 결과는 저장소에 포함하지 않는다.

## 소스 수집

[소스 수집 가이드](SOURCE_COLLECTION_GUIDE.md)는 원출처·전문·OCR 대조·이용조건·중복 검사와 인계 기준을 정의한다. 가이드 작성이나 입력 형식 검증만으로 원문 품질·이용 허락 확인이 완료되는 것은 아니다. `input/`, `scratch/`와 실행 결과는 Git 및 배포 ZIP에서 제외한다.

## 카테고리별 원문·대본 검토 HTML

`review_ui/README.md`에 카테고리당 한 소스 선별 실행, 원문/대본 비교 HTML 생성, 코멘트 자동 저장·JSON 입출력과 추가 배치 병합 방법을 정리했다. `tools/review_batch.py`는 원본 input을 이동하지 않으며, `tools/build_review_site.py`가 실제 생성 결과와 실패 기록을 화면에 묶는다.

요청 대기 한도는 300초다. 전송 실패는 기존 `transport_or_timeout` 코드를 유지하되 새 진단의 `transport_failure`에서 시간 초과와 네트워크 오류를 구별한다. 원문 오류 메시지는 저장하지 않으며 자동 재시도하지 않는다. 과거 진단 없는 실패의 정확한 종류는 소급해 단정하지 않는다.

## 사건 순서 보존 기준 (v13)

원문의 시간·인과 순서나 핵심 사건 배열이 같다는 이유만으로 수정·보류하지 않는다. 표현 복제 지적에는 해당 대본 문장과 원문 문구의 구체적 대응을 요구한다. 실제 표현 복제·사실 왜곡·근거 없는 창작·결말 중복·낭독 규칙은 계속 검사한다. 상세 기준은 `prompts/v13/README.md`다. C01의 한 문장 삭제와 별도 재검토는 `scratch/c01-review-policy-v13-20261108/`에 기록하며 자동 수정 횟수는 2회 그대로다.

## 사실 카드 인용 보강 (v14)

영어 원문의 quote를 번역하지 않고, 한국어 설명은 facts[].text에 쓴다. 낭독 규칙은 script에만 적용하며 인용 구간 내부의 각주·기호·줄바꿈을 보존한다. 상세 규칙은 [v14 안내](prompts/v14/README.md), 두 실패 입력의 실제 재검증 기록은 `scratch/quote-fidelity-v14-20261109/`를 참조한다. 원문 일치 검증과 모델·수정 한도는 유지한다.

## 카테고리 스키마 제한 (2026-11-09)

입력 메타데이터·분석 사실 카드·최종 인계의 category는 공통 `CategoryCode`(`C01`~`C21`)로 제한한다. 분석 요청에 입력 카테고리가 있으면 API 응답 스키마를 그 코드 하나의 enum으로 제한한다. 입력값이 없으면 21개 코드 중 추론할 수 있으며, 수정 단계부터는 최초 확정된 카테고리 하나로 고정한다. `material=null`을 통한 기존 카드 재사용과 사후 원문·유형 검사는 유지한다.

응답 스키마를 단계별 request 로그에 저장하고 재개 비교에 포함한다. 이 변경 전의 스키마 없는 체크포인트나 다른 스키마의 응답은 `checkpoint_config_changed_use_new_destination`으로 중단한다. 기존 기록을 지우지 말고 새 실행 목적지를 사용한다. 단순 retry로 이전 실패 응답을 재사용하지 않는다.

Gemini/OpenAI 요청 본문을 모의 HTTP로 확인했고 전체 회귀171개가 통과했다. 과거 C21의 “C21 성경·신앙” 응답은 새 스키마에서 거부되며 기존 C15 최종 JSON은 유효하다. 스키마 코드 검사 단계에서는 실제 API를 호출하지 않았다. 기록은 `scratch/category-schema-20261109/verification.json`이다.

카테고리 스키마 수정 뒤 C21-03 「룻기 1장」을 실제 재실행했다. category=C21·인용 검사 통과, 7회 호출·최대2회 수정 뒤 실행 완료했고 `output/S2-d64e26d3ed4c045a.json`에 저장했다. 최종 자체 검토는 결말의 질문 회수/해석 구분과 마지막 문장41자 문제로 revise/hold다. 실행 기록은 `scratch/c21-category-schema-20261109/verification.json`. 이전 실패 기록과 원문·기존 output 131개 파일은 보존했다.

### 배치 모드의 현재 제한

배치 모드는 검토 중인 구현입니다. 재개 시 수정된 사실 카드 복원이 불완전한
경로가 있으며, 기존 420편 중 36편은 최종 검토 입력을 근거로 별도 복원했습니다.
이 복원은 배치 구현 자체의 수정이나 품질 승인에 해당하지 않습니다.
체크포인트 재개와 오류 처리 보강은 후속 과제이며, 재개 결과를 인계할 때
최종 검토 입력·대본 claims·material의 일치를 확인해야 합니다.
할인은 Gemini 배치 호출 요금에 해당하며 GPT 실시간 검토 비용에는 적용되지 않습니다.


## Batch 모드 실패·재개 계약 (2026-11-09)

- 새 배치는 작업별 `logs/<content_id>/batch-contract.json`에 설정·전체 프롬프트·스키마를 저장한다. 재개 시 계약과 단계별 요청을 비교하며, 다르거나 기존 로그에 계약이 없으면 `checkpoint_config_changed_use_new_destination`으로 해당 작업을 실패 처리한다. 이전 로그를 삭제하거나 계약 파일을 덧붙여 우회하지 않는다. 기존 출력·복원 기록은 그대로 보존하고 새 제작은 별도 실행 루트를 사용한다.
- 분석→생성→검토→수정→재검토의 검증된 응답을 순서대로 재사용한다. 재개 시 대본·사실 카드·검토 이력·수정 횟수를 함께 복원하며, 저장된 성공 응답은 다시 API로 요청하지 않는다. `revision-N.json`에도 당시 사실 카드를 저장한다.
- 첫/후속 수정 실패와 수정 카드 검증 실패는 해당 작업만 `failed/`로 옮긴다. 이전 수정 응답을 재사용하거나 실패 작업을 최종 출력으로 포장하지 않는다. 수정 과정의 유형·카테고리 및 기존 사실 ID 보존 검사도 적용한다.
- GPT 검토의 호출 기록과 로그 번호는 작업별로 관리한다. `generator.calls`에 Gemini와 GPT의 실제 호출 이력을 모두 포함하고, 캐시 재사용 시 중복 집계하지 않는다. 내부 수정 `revision`과 외부 수정 `feedback_revision`의 별도 예산은 유지한다.
- `logs/batch-jobs/<uuid>.json`은 공유 배치 제출 기록이다. 요청·요청 해시·작업 키·제출 상태·원격 `batch_name`·완료 응답을 저장하고, 각 단계의 `*-batch.json`이 이를 참조한다. 조회/다운로드 중 연결 오류가 나면 작업은 `processing/`에 남는다. 같은 루트와 설정으로 다시 실행하면 원격 ID를 재사용한다. 처리 limit/청크 크기/검토 워커 수를 바꿔도 제출된 작업은 재사용한다.
- 제출 POST 자체는 자동 재시도하지 않는다. 서버 수락 후 응답 유실 또는 ID 저장 직전 중단은 정확한 제출 여부를 알 수 없으므로, 재개 시 `batch_submission_unknown`으로 중단한다. 공유 기록의 `display_name`으로 공급자 측 작업을 확인하고, 일치하는 작업 ID가 확인되면 그 기록의 `batch_name`과 `state="submitted"`를 복구한 뒤 재개한다. 제출되지 않았다는 확인 없이 기록을 지우거나 `prepared`로 되돌리지 않는다. 로컬 파일과 원격 서버 사이의 원자적 제출을 보장한다는 뜻은 아니다.
- 새 계약 이전의 미완료 배치를 자동 변환하지 않는다. 완료된 기존 output은 중복 처리 대상으로 보존한다. 실제 모델 품질·사람 승인·TTS 통과는 이 재개 검증과 별개다.

### TTS 실측 속도 보정 · 2026-11-12

사용자가 제공한 500자당 60초 실측을 반영해 기본 `chars_per_sec=8.333333333333334`, `character_count_mode="with_spaces"`로 보정했다. 현재 500자의 해석은 공백·문장부호 포함 NFC 글자 수이며, 문장 배열을 공백 하나로 이어 센다. 목표 30/60/90초는 각각 약 250/500/750자다. 생성 목표·코드 검사·실시간/배치 결과 추정치·외부 피드백 재검토에 같은 기준을 적용한다. 프롬프트의 공백 제외 규칙은 이 모드에서 명시적 계산 계약으로 재정의한다.

사용자 실측에 근거한 추정이며 개별 산출물의 음성 실측은 아니다. `duration.measured_sec=null`, `tts_checked=false`를 유지한다. 기존 파일의 수치·상태는 일괄 변경하지 않는다. 이전 보정값으로 만든 체크포인트는 새 목적지에서 실행한다. 이전 설정 파일에 모드가 없으면 `non_whitespace`로 읽어 과거의 공백 제외 계산을 보존한다.


## 이슈 수집기의 사실 재서술 검토 연계 (구형 v2/v3 계약)

Handoff의 선택 필드 `use_assessment`는 원문 라이선스와 분리된 사실 재서술 편집 검토 및 기사별 조회·저장·모델 전송 판단이다. 기존 SourceMeta와 입력 해시 계약은 바꾸지 않는다. 기록 없는 기존 생성/배치의 권리 판정도 유지한다. 기록이 있으면 `use_assessment.apply_use_assessment`로 최종 내용 해시·출처 범위·문장별 검토 완료·기존 자체검토 통과 여부를 확인한다. 이슈 수집기가 기사 전문·교차 검증 기록을 대조하고 실제 GPT 검토 후 해당 기록을 발급한다. 메타데이터 표시만으로 우회할 수 없다.

통과한 사실 재서술은 원래 license가 unknown이어도 `rights_status=facts_only_reviewed_not_legal_approval`, `status=ready_for_downstream_review`가 가능하다. 수집·처리 근거가 없으면 별도 `release_status=pending_processing_review`, 유효하면 `pending_editorial_approval`이다. 내용 문제/오래된 검토는 `pending_content_review`/hold, 명시적 처리 금지 또는 기존 이용 제한은 blocked/hold다. 이 상태들은 법적 허가나 공개 승인이 아니며 downstream_approval은 pending으로 유지한다. 게시 연계자는 공개 직전 게이트를 재계산하고 처리 근거·사람 승인을 별도로 확인해야 한다.

외부 피드백으로 대본·근거·자체 검토 이력이 변경되면 이전 검토 해시가 맞지 않아 hold로 돌아간다. 기존 검토는 감사 기록으로 보존하며 수정 예산을 초기화하지 않는다. 이후 이슈 수집기의 `assess-use --demo-id ...`로 재검토한다. 이 PR은 생성기 측 인계 계약만 포함하며 이슈 수집기 실행 코드·운영 도구는 별도 작업이다. 수집기 재검토 명령은 이 PR만 설치한 환경에서는 제공하지 않는다.


## v20 뉴스 맥락 보존 및 자동 검사 (2026-11-12)

v20 도입 시점에 기본 프롬프트를 v20으로 갱신하고 v19를 보존했다. 현재 기본값은 v22다. `issue_collector`의 인계 v2는 사실 블록에 기관 귀속·사건/발표 기준일·유형을 포함한다. 이 정보는 분석부터 생성·검토·수정까지 동일한 source_text로 전달된다. 분석 카드의 text/conditions는 주체·계획 상태를 보존하고 quote는 원문 연속 구간 계약을 유지한다. SourceMeta 스키마나 기존 입력 해시 계산은 바꾸지 않았다.

`news_context.news_checks`를 기존 `validation.checks`에 연결했다. 인계 v2의 명확한 계획 동사를 완료형으로 옮긴 제목/관련 주장과 C08의 알려진 지수명 자리별 오독을 자동 수정 지적으로 만든다. 지수명은 코스닥 백오십·코스피 이백·케이알엑스 삼백으로 읽되 소수부나 식별번호를 일괄 치환하지 않는다. 복합 의미·혼합 시점은 v20 GPT 검토로 확인하며, 단순 패턴 검사만으로 모든 예정/완료 오류를 검출한다고 가정하지 않는다. 공통 검사이므로 실시간·배치·외부 피드백의 기존 수정 예산 및 한도 후 hold 계약이 적용된다. 원문낭독형 원문 보존, 권리 분리, 기존 로그 재사용 제한은 유지한다. 변경된 프롬프트/입력은 새 목적지에서 실행한다.


## v21 사건 중심 뉴스 편집 (2026-11-12)

이 단계에서는 기본 프롬프트를 v21로 변경하고 v20을 보존했다. 현재 기본값과 v4 인계 경계는 아래 v22 절을 따른다. 뉴스 인계 v3는 내부 보도 매체 목록을 낭독용 사실과 구분한다. 신문·방송·통신사 이름과 출처 안내는 제목/script에서 제외하고 원문·메타데이터·로그에서 보존한다. 실제 발표 기관·행위 주체는 유지한다. 첫 한두 문장은 사건과 검증된 주체·날짜로 시작하고 브리핑 안내/한국시간 안내를 쓰지 않는다. 합니다/했습니다/입니다/됩니다체를 유지하며 해요/죠를 섞지 않는다.

`news_context.news_style_checks`는 인계 v3의 위반을 기존 공통 checks에 반환하므로 실시간·배치·외부 수정 모두 같은 수정 예산/hold 계약을 따른다. 매체 목록에는 공식 기관을 넣지 않는다. 기관·날짜·통계명 혼동과 자연스러운 오프닝은 v21 GPT 검토에서 확인한다. 날짜·인용·입력 해시 및 권리 분리 계약은 유지한다. 구형 v2는 읽을 수 있으며 기존 결과를 소급 재작성하지 않는다. 새 프롬프트/인계 포맷은 새 실행 목적지를 사용한다.

실제 데모에서 발견한 6월/10월의 비관용 낭독(육월/십월)도 v3 뉴스 코드 검사에서 지적한다. 원문·quote를 바꾸지 않고 script에 유월/시월을 적용한다. 최종 데모와 자동 수정/직접 편집의 구분은 로컬 수집기 실행 기록에 보존하며, 해당 실행 산출물은 이 PR에 포함하지 않는다.


## v22 독립 사실 소스 인계 (과거 v4 입력 호환)

현재 기본 프롬프트는 v27이며 다음은 v22 당시 계약이다. 이슈 수집기의 인계 v4는 기사 대조 후 독립적으로 작성한 사실 자료만 받는다. 기존 스키마에서 `source_id=ICF-...`, `author="이슈 수집 에이전트"`, `source_kind="original"`, `source_url=null`, `license.kind="CC0-1.0"`, `attribution=""`로 식별한다. original은 새로 작성된 입력 자료를 뜻하며 원기사를 뜻하지 않는다. 일반 user_summary의 원출처 기록 계약은 변경하지 않는다.

`fact_source.fact_metadata`와 SourceMeta/SourceSnapshot 직렬화가 이 계약에 한해 빈 `original_sources` 키를 제외한다. 원기사 목록·URL 또는 잘못된 source_kind/라이선스가 붙으면 거부한다. 실시간·배치·외부 피드백 요청과 최종 JSON에 같은 경계를 적용한다. `complete_license`는 명시된 CC0의 조건과 근거를 검사하고 attribution을 요구하거나 자동 생성하지 않는다. CC0는 입력 제공자의 보유 권리에 대한 공개 선언이며 법적 무해성이나 서비스 게시 승인이 아니다.

원기사와 처리 권한 판단은 이슈 수집기 내부에 둔다. 새 Handoff에는 원기사별 use_assessment를 넣지 않으며, 수집기의 별도 최종 원기사 대조를 거쳐 상태만 반영한다. 수집기 외부 피드백 도구는 내용이 바뀌면 이전 판단을 무효화하고 재검토를 요구한다. 대본 생성기 단독 결과를 수집기의 원기사 대조 완료로 해석하지 않는다. 기관 귀속·날짜·예정/완료·낭독 규칙과 뉴스 문체 검사는 v4에도 적용한다. 기존 v21 입력·데모·로그는 보존하며 새 계약은 새 실행 목적지를 사용한다.

로컬 기본 생성기 provider는 `gemini`이며 배치 모드에서 프로젝트 `.env`의 Gemini API 키를 사용한다. 환경변수의 우선순위는 유지한다. API 키와 서비스 계정 설정이 함께 있으면 `gemini`는 API 키를 우선하고, 명시적인 `vertex`는 standard 모드에서 기존 서비스 계정 인증을 사용한다. 서비스 계정만 있는 기존 Gemini 설정의 호환 경로도 standard 모드에서 유지한다.

## 이전 v22 배포 범위와 검증 · 2026-11-12

v22 업데이트는 script_agent의 v19~v22 프롬프트·인계·검증 변경이었다. 당시 기존 PR의 source_collector 구현은 유지했고 로컬의 후속 수집기 변경 및 issue_collector 자체는 포함하지 않았다. 현재 이슈 수집기와 v6 인계는 위 계약을 따른다. 독립 사실 소스의 수집·원기사 대조·배포 승인 절차는 수집기 담당 범위이며, 생성기는 전달된 입력의 형식과 대본을 검사한다. 기존 출력·원문·로그·자격증명은 배포하지 않는다.

기존 PR에서 제공하던 인증 의존성 잠금, 일반화된 비밀 파일 제외 규칙 및 소스 수집 안내는 유지한다. 변경된 기본 설정·프롬프트로 과거 미완료 체크포인트를 재개하지 않고 새 목적지를 사용한다. 실제 모델 호출·TTS·서버 배포 없이 Gemini 기본 설정의 분리된 소스 사본에서 회귀를 검증한다.

통합 서버와의 실제 연결 검사 결과 및 실행 명령은 [one-server 호환성 검사](tools/integration/README.md)를 참조한다. 생성기 단독 회귀 통과와 자동 제작 전체 호환성은 별개이며, 현재 통합부에는 출력 탐색·게시 조건·hold 수정본 전달·배치 재개 관련 미충족 계약이 있다.
