# AI 창작 사연 출처 정책 · 2026-11-13

C01~C05의 생성 경로는 AI가 새로 쓰는 허구의 사연이다. 외부 출처·Instagram URL·원천목록 ID·attribution을 만들지 않는다. Markdown은 source_url=null, source_kind=original(자체 작성 입력), attribution=""이며 AI 창작임을 notes에 명시한다. 원장은 URL과 원천목록 ID를 비우고 원천 명칭을 AI 창작물로 표시한다. 웹페이지 수집·대조 완료나 실화라고 기록하지 않는다. 이 지침은 아래의 Instagram 실원문 필수 안내보다 우선한다. 실제 수집 자료의 출처는 유지하며 기존 산출물은 소급 변경하지 않는다. 품질·개인정보 검사는 유지한다.

# Source Collector Harness (`source_collector`)

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


[SOURCE_COLLECTION_GUIDE.md](SOURCE_COLLECTION_GUIDE.md) 표준 운영 절차(SOP)를 바탕으로 구현한 **오디오 숏폼 원천 소스 수집·중복 검증·헤드리스 AI 퀄리티 평가(LLM-as-a-Judge) 하네스**입니다.

---

## 1. 디렉터리 구조

```text
ai/source_collector/
├── collector                        # 통합 실행 CLI 래퍼 스크립트
├── run_tmux.sh                      # SSH 종료 후에도 유지되는 tmux 백그라운드 실행 스크립트
├── AGENTS.md                        # Antigravity CLI 워크스페이스 자동 지침
├── pyproject.toml                   # 패키지 및 pytest 설정
├── SOURCE_COLLECTION_GUIDE.md       # 소스 수집 표준 운영 절차(SOP) 가이드
├── 전체_소스_목록.csv               # 로컬 동기화 마스터 원장 (UTF-8-SIG, 15컬럼)
├── all_sources_summary.csv          # 영문 파일명 호환 마스터 원장
├── data/                            # 참조 데이터 로컬 사본
│   ├── 원천목록.csv                 # C06~C21 공인 출처 카탈로그 (SRC-001 ~ SRC-076)
│   ├── 컨텐츠 제작 관리.txt         # 골드 스탠다드 벤치마크 및 기존 데모 목록
│   └── 전체_소스_목록.csv           # 마스터 원장 백업본
├── src/source_collector/
│   ├── __init__.py
│   ├── config.py                    # 카테고리(C01~C21), 라이선스, 표준 경로 설정
│   ├── models.py                    # 후보 소스 및 검증 결과 데이터 모델
│   ├── prescreen.py                 # 사전 중복 스크리닝 엔진 (CSV 1,050건 + 데모 + URL 대조)
│   ├── generator.py                 # 카테고리별 원문 생성·추출기 (인스타 사연 / 공인 지식 서사)
│   ├── judge.py                     # 헤드리스 LLM-as-a-Judge 평가 및 9점 미만 자동 승급(Re-elevation)
│   ├── writer.py                    # 규격 Markdown 생성기 및 script-agent validate-input 검증기
│   ├── ledger.py                    # 이중 CSV 동기화 및 collection-log.md 원장 기록기
│   ├── harness.py                   # 3단계 파이프라인 오케스트레이터
│   └── cli.py                       # CLI 엔트리포인트 (run, prescreen, evaluate, status)
└── tests/
    └── test_harness.py              # 단위 및 통합 테스트 스위트
```

---

## 2. 핵심 기능

1. **저장 모드 3종 지원 (`--mode`)**:
   - `staging` (기본값): `ai/script_agent/input/` 폴더를 일체 건드리지 않고 `ai/source_collector/output/<batch_id>/`에 검증 완료된 원본만 저장합니다.
   - `direct`: 기존 대기 입력을 유지하고 신규 채택 소스의 임시 실행 사본만 `input/`에 투입합니다.
   - `sandbox`: 프로덕션 원장(`전체_소스_목록.csv`)이나 `good/` 폴더를 변경하지 않고, `sandbox_output/` 및 `sandbox_library/`에서 독립적으로 수집·헤드리스 평가를 테스트합니다.
2. **사전 중복 스크리닝 (`prescreen.py`)**:
   - AI 평가 호출 전, `전체_소스_목록.csv`(1,050건) + `demo/컨텐츠 제작 관리.txt` + 인스타그램 URL과의 교집합을 전수 검사하여 등록된 제목·URL 검사에 통과한 후보만 평가 단계로 넘깁니다.
3. **헤드리스 AI 퀄리티 평가 & 자동 승급 (`judge.py`)**:
   - 독립 프로세스(`agy --model gemini-3.8-flash-medium --dangerously-skip-permissions -p "..."`)로 골드 스탠다드(`S01`, `S05`, `H01`, `I02`, `M04`) 대비 10점 만점 채점을 수행합니다.
   - **9점 미만(`SCORE < 9`)** 발생 시 탈락 사유(`REASON`)를 피드백으로 주입하여 9~10점 수준으로 즉시 재작성(Re-elevation) 후 재평가합니다.
4. **CLI 스키마 검증 (`writer.py`)**:
   - 생성된 모든 `.md` 파일에 대해 `script-agent validate-input`을 자동 호출하여 `{"valid": true}` 통과 여부를 검증합니다.
5. **이중 마스터 원장 실시간 동기화 (`ledger.py`)**:
   - 채택된 소스를 15개 정규 컬럼(`소스ID` 맨 앞, `구분` 제외, `UTF-8-SIG`)으로 `전체_소스_목록.csv`, `all_sources_summary.csv`, `collection-log.md`에 동시 기록합니다.

---

## 3. CLI 사용법

### 3.1 현재 원장 및 디렉터리 현황 조회 (`status`)
```bash
./collector status
```

### 3.2 전체/특정 카테고리 수집 및 헤드리스 평가 실행 (`run`)
```bash
# 1) Staging 모드 (input/ 보존, output에 C01~C21 각 25개씩 수집)
./collector run --mode staging --categories all --count 25

# 2) 특정 카테고리만 지정 수집 (원본은 output에 저장)
./collector run --mode staging --categories C01,C13 --count 5

# 3) Direct 모드 (기존 대기열 유지, 신규 소스를 input/에 투입)
./collector run --mode direct --categories all --count 20

# 4) Sandbox 모드 (프로덕션 CSV/폴더 수정 없이 헤드리스 파이프라인 테스트)
./collector run --mode sandbox --categories C01,C13 --count 1 --json
```

### 3.3 제목·URL 중복 사전 검사 (`prescreen`)
```bash
./collector prescreen --title "검사할 사연 또는 지식 제목" --category C01
```

### 3.4 단일 Markdown 파일 헤드리스 AI 평가 (`evaluate`)
```bash
# 단순 점수 측정
./collector evaluate /path/to/source.md

# 9점 미만일 경우 자동 보강(Re-elevate)까지 수행
./collector evaluate /path/to/source.md --elevate
```

---

## 4. `tmux` 백그라운드 실행 및 Antigravity CLI (`agy`) 연동

서버에 `Linger=yes` 및 독립 `agy` 바이너리가 설정되어 있으므로, `tmux`로 실행하면 **SSH 연결이나 IDE를 완전히 종료해도 끝까지 수집·평가를 완수**합니다.

### 4.1 `run_tmux.sh` 스크립트로 원클릭 백그라운드 실행 (권장)

```bash
cd <저장소 경로>/ai/source_collector

# 방법 A) 하네스 파이프라인 직접 백그라운드 가동
# 기본값: staging 모드로 good/ 폴더에 C01~C21 각 20개씩 수집
./run_tmux.sh start

# 원하는 카테고리/수량 지정 실행 (예: 전 카테고리 25개씩 good/에 수집)
./run_tmux.sh start run --mode staging --categories all --count 25

# 방법 B) agy 에이전트에게 자연어 프롬프트로 지시하여 백그라운드 가동
./run_tmux.sh agy "세부 카테고리별로 20개씩 good 폴더에 추가로 수집해줘"
```

#### 진행 상황 확인 및 제어 명령어
```bash
./run_tmux.sh status   # tmux 세션 실행 여부 확인
./run_tmux.sh logs     # 실시간 진행 로그 스트리밍 (collector_tmux.log)
./run_tmux.sh attach   # 실행 중인 tmux 화면으로 직접 접속 (빠져나오기: Ctrl+B 누른 뒤 D)
./run_tmux.sh stop     # 실행 중인 백그라운드 세션 강제 종료
```

### 4.2 직접 `tmux` 세션을 열고 대화형 `agy` CLI 사용하기

이 폴더(`ai/source_collector`)에는 [`AGENTS.md`](AGENTS.md)와 [`.agents/rules/source-collection-sop.md`](.agents/rules/source-collection-sop.md)가 내장되어 있어, 별도의 `@파일` 태그 없이 자연어로 지시해도 즉시 하네스와 원장을 인식합니다.

SSH 연결을 끊어도 권한 승인 창에서 멈추지 않도록 `--dangerously-skip-permissions` 옵션을 붙여 실행하세요:

```bash
tmux new -s collector
cd <저장소 경로>/ai/source_collector
agy --dangerously-skip-permissions

# 프롬프트 입력 예시:
# > "세부 카테고리별로 20개씩 good 폴더에 수집해줘"
# > "input 폴더 비우고 C01~C05 10개씩 새로 수집해서 input에 넣어줘"

# 지시 후 화면 분리(Detach): Ctrl+B 누른 뒤 D 입력 -> 이후 SSH 종료 가능
```

---

## 5. 테스트 실행

```bash
.venv/bin/pytest -v
```


## 설치 및 로컬 자료

저장소 루트에서 `ai/script_agent/README.md`에 따라 생성기 환경을 먼저 설치합니다.
수집기는 `ai/source_collector`에서 다음과 같이 설치합니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e . pytest
.venv/bin/python -m pytest -q
```

실제 수집에는 인증된 `agy` CLI가 필요합니다. 원장 CSV, `data/` 참조 자료,
수집 원문, sandbox 결과 및 `../script_agent/good/`는 Git에 포함하지 않습니다.
기존 소스와 중복을 확인하려면 운영 원장을 `../script_agent/전체_소스_목록.csv`에
별도로 준비해야 합니다. 원장이 없으면 기존 수집 이력에 대한 중복 검사는 수행되지 않습니다.
참조 자료는 위 디렉터리 구조의 `data/` 경로에 준비합니다. 테스트는 임시 시험 원장을 사용합니다.

## 현재 검증 범위와 후속 과제

이 하네스는 검토 중인 구현입니다. 모델 채점과 입력 스키마 검사는 원문 실재 여부,
사실 정확성 또는 이용 권한 검증을 대신하지 않습니다. 현재 누락 URL을 생성하는
fallback과 출처별 이용 조건을 일괄 부여하는 경로가 있어, 채택 결과의 실제 URL과
개별 이용 조건을 확인하기 전에는 검증 완료 소스로 취급하지 않습니다.
실제 원문 조회 근거 및 권리 확인을 강제하는 처리는 후속 보완 대상입니다.

## 개인정보 검토 결과 · 2026-11-10

기존 `../script_agent/good/` 525편을 읽기 전용으로 패턴 검사했다. 본문 탐지
0편, 메타데이터 탐지 132편이며 규칙별 파일 수는 핸들 125·이메일 33·전화번호
9편이다(중복 포함). 이는 형식상 탐지 건수이며 실제 개인 연락처 여부나
전체 개인정보 부재 판정이 아니다. 기존 파일은 변경하지 않았다.
기존 구현은 사연 생성 시 계정명을 요구·보충하고 author/attribution/notes 및
원장에 전파했다. 이번 변경은 새 실행의 그 경로를 익명 작성자 계약과 저장 전
강제 검사로 차단한다. 실행 경로는 직접 HTTP가 아니라 `agy` 서브프로세스이며
그 응답을 검사한다. 실제 모델 호출 없이 모의 응답으로 회귀 검증했다.
