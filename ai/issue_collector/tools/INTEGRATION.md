# factory 앞단 연결 계약

`collect_to_inbox.sh`는 수집 → 검증 → 원문 포함 Markdown 저장 → 생성기 입력함 전달을 한 번에 수행한다. `bash tools/run.sh run`과 같다. factory·대본 생성·TTS는 이 명령이 실행하지 않는다. 실행 위치와 무관하게 스크립트가 수집기 루트를 찾는다.

## 설치와 인증

Python 3.11 이상에서 수집기를 한 번 설치한다. 수집만 할 때 script_agent 설치는 필요 없다.

```bash
cd <저장소 경로>/ai/issue_collector
python3 -m venv .venv
.venv/bin/pip install -e .
```

기본 인터프리터는 수집기 `.venv/bin/python`이며 `ISSUE_COLLECTOR_PYTHON=/absolute/path/to/python`으로 교체할 수 있다. 설정과 `.env`는 모두 수집기 루트에서 읽는다. factory의 `teum.env` 또는 생성기의 `.env`를 수집기가 직접 읽지는 않는다. factory가 환경변수로 넘긴 값은 사용할 수 있다.

| 수집기 `.env` 이름 | 현재 설정에서의 용도 |
|---|---|
| `openai_api_key` | 필수: OpenAI 사실 검증 |
| `gemini_api_key` | 필수: 현재 Gemini Batch API의 선정·추출·보충 검색 |
| `google_application_credentials` | 선택적 Vertex standard 모드의 서비스 계정 경로. 기본 배치에는 사용하지 않음 |
| `gcp_project_id` | 선택적 Vertex 프로젝트 |
| `gcp_location` | 선택적 Vertex 리전. 생략 시 `us-central1` |
| `X-NCP-APIGW-API-KEY-ID` / `X-NCP-APIGW-API-KEY` | Naver 뉴스 검색. 두 값을 함께 설정. 없으면 다른 수집 경로 사용 |
| `naver_client_id` / `naver_client_secret` | NCP 키가 둘 다 없을 때 사용하는 구형 Naver 인증 쌍 |

환경변수가 `.env`보다 우선한다. OpenAI·Google 키는 `.env`에서는 표의 소문자 이름을 쓰고, 프로세스 환경변수에서는 대문자 이름도 지원한다. NCP 키 이름은 하이픈을 포함한 그대로 `.env`에 작성한다. `.env`를 셸 `source`로 실행하지 않는다. 서비스 계정 상대 경로는 현재 `config.toml`의 `credentials_root = "../script_agent"` 기준이며 키 파일을 복제하지 않는다.

## 명령과 상한

```bash
# 확인만: 인증 읽기·API 호출·파일 쓰기 없음
./collect_to_inbox.sh --limit 5 --inbox /absolute/path/to/input --dry-run

# 실제 수집 및 전달: 날짜를 생략하면 KST 전날
./collect_to_inbox.sh --limit 5 --inbox /absolute/path/to/input

# 날짜 고정. 동일 명령 재호출 시 동일 계약의 결과/전달 기록 재사용
./collect_to_inbox.sh --date 2026-11-12 --limit 5 --inbox /absolute/path/to/input
```

| 옵션 | 계약 |
|---|---|
| `--limit N` | 기본 5, 1~40. 최종 소재의 시사+시장 합계 상한 |
| `--inbox PATH` | 기본 우선순위: 명시 옵션 → `TEUM_INBOX` → `TEUM_HOME/script_agent/input` → 인접 `script_agent/input` |
| `--date YYYY-MM-DD` | 실행일이 아닌 수집 대상 사건일. 기본 KST 전날 |
| `--run-id ID` | 기본 `inbox-daily-v1`. 날짜별로 별도 저장. 영문·숫자·하이픈·밑줄 허용 |
| `--dry-run` | 배분·경로 확인만 수행 |
| `--mode batch\|standard` | `[collection] mode` 재정의. 현재 기본값은 batch |

시사 `floor(N/2)`, 시장 `ceil(N/2)`를 수집 전에 배정한다. 1→0/1, 5→2/3, 6→3/3. 할당 0인 분야는 검색/모델 호출도 하지 않는다. 분야별 상한에 도달하면 추가 주제 추출을 멈춘다. 후보 탐색과 사실 교차 검증에는 여러 기사/모델 호출이 필요하므로 N은 원기사 다운로드 수나 API 호출 횟수의 한도가 아니다.

휴장·근거 부족 등으로 한쪽을 채우지 못하면 다른 쪽에 추가 할당하지 않는다. 요청 편수가 모두 확보되지 않은 실행은 수집 결과를 보존하고 입력함에는 전달하지 않는다. 이미 완료된 부분 수집을 재호출해도 반복 호출로 편수를 채우지 않는다. 새 수집이 필요하면 명시적으로 새 실행 ID를 사용한다. 상한은 날짜+실행 ID별이며, 별도 ID를 여러 번 만들면 하루 전체 상한이 되지는 않는다.

## factory 설정

factory의 `TEUM_HOME/teum.env`에 아래 값을 넣는다. 예시의 수집기 경로를 배포 경로로 바꾸고 `{inbox}`는 그대로 둔다. 경로에 공백이 있어도 사용할 수 있다.

```dotenv
TEUM_COLLECT_CMD='bash "<저장소 경로>/ai/issue_collector/collect_to_inbox.sh" --limit 5 --inbox "{inbox}"'
TEUM_COLLECT_INTERVAL=86400
```

factory는 명령을 `shlex.split`으로 나눈 뒤 `{inbox}`를 자신이 쓰는 `TEUM_HOME/script_agent/input/`으로 바꾸고 `TEUM_INBOX`에도 같은 경로를 전달한다. 셸 연결 연산자·환경변수 확장에 의존하지 않는다. 이 수집기의 `--limit`은 수집량이고 factory의 `TEUM_FACTORY_MAX_GENERATE`는 한 바퀴의 생성량이므로 서로 독립이다.

현재 factory는 수집 1,800초·생성 3,600초·외부 수정 1,800초 제한을 적용하며 수집 실패도 다음 수집 주기까지 기다린다. 반면 Gemini 배치는 장시간 대기할 수 있고, 두 프로그램의 기본 `[batch] wait_timeout_sec=86400`은 **한 원격 작업**의 조회 상한이다. 여러 의존 단계가 있어 전체 실행은 더 길 수 있다. 연결 담당자는 factory 제한 시간을 충분히 늘리거나, 대기 종료 후 **같은 날짜·실행 ID/출력 목적지/수정 버전**을 재조회하도록 연결해야 한다. 현재 factory 코드는 이 변경으로 자동 수정되지 않는다. 다음날의 기본 날짜로 새 수집을 시작하는 것은 전날 pending 작업의 재개가 아니다.

`[collection] mode="batch"`는 선정·추출·재추출·Google Search 요청 모두에 적용한다. 입력함 전달 명령을 새로 만들 필요는 없다. 수집기는 이전 단계 응답이 필요한 단계들을 순차 배치로 제출하며 실시간으로 우회하지 않는다. 원격 조회 실패/대기 상한에 도달하면 `status=pending`, 종료 코드 2로 반환하고 입력함에 넣지 않는다. 같은 날짜·ID를 재호출하면 기존 원격 ID를 조회한다. `collect`만 사용할 때는 원래 인자에 `--resume`을 붙인다. 구형 standard 계약은 재사용하지 않으므로 최초 전환에는 새 `--run-id`를 쓴다.

## 출력과 종료 코드

stdout은 JSON 객체 한 개, 진행 로그는 stderr다. 성공 JSON에는 `status=complete`, `target_date`, `run_id`, `limit`, `requested_counts`, `collected_counts`, `collected`, `delivered`, `already_delivered`, `files`, `inbox`, `path`, `receipt_path`가 있다. `delivered`는 이번에 새로 넣은 수, `already_delivered`는 과거 전달/동일 파일로 확인한 수다. 입력함에서 이미 소비된 파일도 후자에 포함되며 다시 만들지 않는다. `files`는 이번 실행의 인계 목록이지 현재 입력함 재고가 아니다.

| 종료 코드 | 의미 / 연결 측 처리 |
|---|---|
| 0 | 전달 완료 또는 dry-run 계획 확인. 재호출로 신규 전달 0편이어도 정상 |
| 2 | 배치 대기 `code=batch_pending_resume_same_run`, 또는 수집 부족/부분 실패 `code=collection_incomplete_no_delivery`. 신규 전달 0편. CLI 문법 오류도 argparse 규칙에 따라 2 |
| 1 | 계약 변경·인증/실행 오류·전달 충돌·불명확한 전달. 오류 JSON의 `code` 확인 |

`path/manifest.json`이 수집 기준이다. 출력 폴더 전체 glob 대신 manifest의 `ready_for_script` 항목만 전달한다. 수집 산출물·원문은 이동하지 않으며 `.md` 내용은 바이트 단위로 보존한다. 카드 JSON·캐시·모델 로그·별도 메타 파일을 입력함에 넣지 않는다.

## 재실행과 전달 복구

- 같은 날짜·실행 ID는 코드·설정·프롬프트·분야별 상한이 같은 경우만 재사용한다. 바뀌면 `contract_changed_use_new_run_id`로 중단한다. `run`은 기존 수집의 검증된 단계 재개를 자동 요청한다. 배치의 `batch.json`에 요청 해시·제출 의도·원격 ID·완료 응답을 저장한다. 조회 실패는 같은 ID로 다시 조회하고, 제출 응답 유실은 `batch_submission_unknown`으로 멈춰 공급자 확인을 요구한다. 실패·취소·만료 배치를 성공 응답으로 포장하거나 실시간 요청으로 대체하지 않는다.
- 입력함 `.issue-collector/receipts.json`은 파일명·SHA-256·전달 상태를 저장한다. 입력 파일을 생성기가 `processing/`이나 archive로 옮겨도 영수증은 남는다. 이 숨김 디렉터리를 보존해야 중복 방지가 유지된다. 다른 수집 실행도 같은 입력함에서는 이 기록을 공유한다.
- 입력함과 같은 파일시스템의 숨김 임시 파일에 쓰기를 끝낸 뒤 hard link로 최종 `.md`를 원자적으로 노출한다. 부분 작성 파일은 생성기에 보이지 않고 기존 파일을 덮어쓰지 않는다. POSIX hard link·파일 잠금·fsync를 지원하는 로컬 파일시스템을 사용한다.
- 파일명은 같은데 내용이 다르면 충돌로 중단한다. 동일 파일이 이미 있거나 전달 완료로 기록돼 있으면 중복 투입하지 않는다. 동시에 실행된 전달은 입력함 잠금으로 직렬화하고, 잠금 획득에 실패한 호출은 오류로 종료한다.
- 전달 중 중단되면 일부 파일은 이미 입력함에 들어갔을 수 있다. 재호출은 확실한 완료 기록을 건너뛰며, `pending` 상태의 파일이 입력함에 남아 있으면 해시 확인 후 완료 기록을 복구한다. `pending`인데 파일이 없으면 소비된 것인지 게시 전 중단인지 구별할 수 없어 `delivery_unknown_requires_check`로 멈춘다. 자동 재투입하지 않는다. 연결 담당자가 생성기 `processing/`·archive와 대조해 복구해야 한다. 영수증 삭제나 새 실행 ID만으로 우회하지 않는다.

수집·입력함 전달 성공은 대본 품질·기사 처리 권한·공개 승인 상태를 변경하지 않는다. factory의 기존 후속 단계가 이를 판단한다.
