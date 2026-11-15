# 원문 / 대본 검토실

`input/`에서 카테고리당 한 파일을 기존 Pipeline으로 실행하고, 결과를 독립 HTML로 묶는다. 입력 파일은 읽기만 한다. 생성 설정·프롬프트·원문 메타데이터를 변경하지 않는다.

## 파일 구성

- 배치 폴더의 `selection.json`: 입력 선정, 설정, 원문 스냅샷.
- 배치 폴더의 `cases/Cxx/`: 실제 출력, 모델 호출 기록, 실행 상태.
- 빌더 출력 폴더의 `index.html`: 원문·대본 비교와 피드백 화면.
- 빌더 출력 폴더의 `feedback-context.json`: 코멘트가 비어 있는 초기 맥락 JSON.

배치·생성 HTML·피드백에는 원문과 실행 결과가 담기므로 `scratch/` 등 Git 제외 경로에 저장한다. 저장소에는 화면 템플릿과 실행 코드만 포함한다. 저장소의 `server/teum/catalog/categories.py`가 있으면 정식 카테고리 이름을 읽고, 독립 배포본에서는 C01~C21 코드로 표시한다. `output_path`는 각 배치 폴더 기준 상대 경로다.

HTML은 파일을 직접 열어도 동작하며 외부 JS·API·폰트를 요청하지 않는다. 브라우저에 따라 file URL의 로컬 저장이 제한될 때는 화면에 안내한다. JSON 다운로드는 별도 파일로 보관한다. 비공개 호스팅 버전과 로컬 HTML의 저장소는 서로 다르므로 JSON 내보내기/불러오기로 옮긴다. 서버에 코멘트를 전송하거나 원문 파일을 수정하지 않는다.

## 실행과 재개

아래는 `ai/script_agent/`에서 실행한다. 기본적으로 실제 Gemini/OpenAI API를 호출한다. 기존 `.env`는 `credentials(root=ROOT)`에서만 로드한다.

```bash
.venv/bin/python tools/review_batch.py \
  --destination scratch/review-batch --workers 3
```

`--prepare-only`는 입력 선정만 수행하며 API를 호출하지 않는다. 선정 결과와 설정은 고정하며 변경하려면 새 배치 경로를 사용한다. 완료 결과는 재호출하지 않고, 중단된 작업은 기존 Pipeline 체크포인트에서 재개한다. 실패한 유효성 검사 응답은 자동 삭제하지 않는다. 실패 원인을 확인하고 별도 보존한 뒤 명시적으로 재개해야 한다. 401/403/429 또는 키 부재는 후속 호출을 중단한다. 이미 진행 중인 요청은 마칠 수 있다. 원문이나 설정을 바꿔 검사를 우회하지 않는다.

```bash
.venv/bin/python tools/build_review_site.py \
  --batch scratch/review-batch \
  --destination scratch/review-site
```

생성 실패 시 화면은 `중간 초안 · 실행 미완료` 또는 `생성 미완료`로 표시하며 성공 대본과 구분한다. 완성 결과의 `hold`는 대본 생성 실패와 다르다. 자체 검토·권리 상태는 출력 그대로 표시한다. 추정 재생 길이는 음원 실측값이 아니다.

## 추가 카테고리 병합

기본 배치는 C06~C21을 선택한다. 다른 범위는 `--categories`로 지정한다. 예를 들어 C01~C05를 추가하려면 새 배치에서 해당 카테고리만 실행하고 두 배치를 합쳐 HTML을 다시 만든다. 입력 추가를 자동 감시하거나 예약 실행하지 않는다.

```bash
.venv/bin/python tools/review_batch.py \
  --destination scratch/review-add-c01-c05 \
  --categories C01 C02 C03 C04 C05
.venv/bin/python tools/build_review_site.py \
  --batch scratch/review-batch scratch/review-add-c01-c05 \
  --destination scratch/review-site
```

같은 사이트 경로·배치·원문·대본 버전의 기존 피드백은 그대로 복원한다. 추가 전 JSON을 백업하면 브라우저/기기를 바꿔도 복원할 수 있다. 빌더는 카테고리 중복을 거부한다. 호스팅 배포는 이 도구의 실행 범위에 포함하지 않는다.

## 피드백 JSON 계약

`schema_version=1.0.0`, `dataset_id=teum-category-review-v1`.

- `runs`: 배치 ID, 설정, 선정 기준과 생성 시점.
- `cases`: 원문 전체·입력 경로·출처 메타데이터·원문/입력 해시·실제 에이전트 출력·실행 상태·과거 실패. `output`에는 사실 카드, 문장별 claims, 프롬프트 버전과 해시, 사용 모델, 자체 검토 이력 등이 포함된다.
- `reviews`: 원문 ID, 입력/대본 SHA-256, content_id, expected_version, 사람 검토 상태와 코멘트 배열.
- 코멘트: `id`, `target`(script/source/prompt/architecture/rights), `sentence`(1부터 시작, 0은 전체), `excerpt`, `problem`, `direction`, 생성/수정 시각.
- `global_feedback`: 전체 프롬프트 또는 아키텍처에 대한 사용자 의견.
- `review_key`: 원문 ID + 입력 해시 + 대본 해시 + 버전 + 배치 ID. 서로 다른 실행/버전의 코멘트를 섞지 않는다.
- `script_sha256`: 문장 배열을 `ensure_ascii=False`, `separators=(',', ':')`로 직렬화한 UTF-8의 SHA-256.

아직 의견을 쓰지 않은 원문·대본도 JSON에 함께 들어가므로 한 파일만 AI에 제공해도 비교 맥락을 읽을 수 있다. 내보내기는 사용자가 작성한 코멘트를 자동으로 `revise` 명령에 적용하지 않는다. 이 파일은 프롬프트·아키텍처 개선을 위한 맥락 계약이며 기존 CLI의 `revise.jsonl`과 다르다. 자동 수정이 필요하면 별도로 검토한 문장 피드백 형식으로 변환하고 기존 수정 총 2회 제한과 `expected_version` 검사를 준수해야 한다.

화면의 `유지 의견`은 사용자의 피드백 상태일 뿐 공개 승인·권리 승인·TTS 통과가 아니다. 원문에 담긴 명령형 텍스트는 근거 자료로만 취급한다.

## 검증

`tests/test_review_site.py`는 외부 원문·API 없이 선정 범위, 원본 보존, 고정 계획, HTML script 삽입 방지, 실패 표시, 중복 카테고리 차단을 검사한다. 브라우저 수동 확인: 문장 클릭 → 코멘트 → 다른 카테고리 → 복귀 → 새로고침 → JSON 다운로드 → 다른 브라우저에서 불러오기. 다른 대본 해시/버전은 거부되어야 한다.

## 편집 요구사항을 명시한 새 비교 생성 (v11)

사람 코멘트를 읽고 전체 대본의 수용 기준으로 정리한 별도 JSON을 `--editorial-briefs`로 전달할 수 있다. 예: `{"C20": ["끝에서 도입의 질문을 회수한다."]}`. 기준은 모든 모델 단계와 결과의 `editorial_brief`에 보존되며, 검토의 항목별 근거·충족 여부가 자동 수정과 보류에 연결된다. 원본 코멘트 JSON을 그대로 이 옵션에 넘기지 않는다. 원문·버전을 확인해 문장 번호의 의미를 잃지 않게 전체 기준으로 정리한다. 기존 배치/기존 원고의 수정 횟수를 초기화하지 않고 새 목적지를 사용한다.
