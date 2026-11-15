# teum_refine — 대본 정제(검수) v1.0.5

대본 생성 다음, TTS 앞에서 대본을 한 편씩 검사해 판정과 수정 사유를 낸다. 대본을 직접 고치지는 않는다.

```
[대본 생성(Gemini·GPT)] --대본 JSON--> [정제] --pass-->           [목소리·TTS]
                                          --revise + 수정 사유--> [대본 수정 단계]
                                          --human_review-->       [사람 확인]
                                          --reject-->             폐기
```

## IN — 무엇을 넣나

세 가지 입력을 받는다. 모두 `src/teum_refine/input.py`·`site.py`에서 같은 내부 형식으로 바뀐다.

**1. 대본 생성기 출력 JSON (기본, `schema_version` 1.0.0)** — 대본 한 편 = JSON 한 개

| 키 | 필수 | 내용 |
|---|---|---|
| `content_id` | ✓ | 관리번호. 출력 파일 이름이 된다(영문·숫자·`-`·`_`) |
| `title` | ✓ | 제목 |
| `production_type` | ✓ | 제작 유형. `창작·각색(형)` `사실해설(형)` `사실서사(형)` `실행안내(형)` `해석·감상(형)` `원문낭독(형)` — "형"·가운뎃점 유무 상관없음 |
| `script` | ✓ | 낭독 본문. 배열 한 칸에 한 문장. 숫자·영문은 한글 읽기로 |
| `sources[]` | ✓ | 원천. `source_id`, `source_url`, `author`, `license{kind, commercial, modify, share_alike}`(null이면 권리 미확인), `attribution`, `text`(수집 당시 원문 — 원문 대조에 필요) 또는 `text_path`, `info_as_of` |
| `sources[].source_kind`, `sources[].original_sources` | | 사실해설형의 이슈 수집기 원문 인계: `source_kind: user_summary`이고 `original_sources`가 비어 있지 않으면 R1은 원기사 사실 이용·매체 표기 확인 경고. `site_grade`가 있으면 사이트 이용 등급을 우선 적용 |
| `material.facts[]` | | 사실 카드 `{id, text, quote, kind, conditions[]}`. `quote`는 본문 증거, `text`·`conditions`는 조회 힌트. 카드 주석만으로 조건 누락을 수정 요구하지 않음 |
| `sources[].info_as_of`, `material.info_as_of`, `material.as_of`, `created_at` | | R3-b의 `reference_date` 후보. 이 순서로 처음 찾은 `YYYY-MM-DD` 접두부를 사용(원천은 배열 순서). 없으면 생략 |
| `claims[]` | | 문장↔사실 연결 `{sentence, kind, fact_ids[]}`. 사실 대조의 힌트. `kind: quotation`은 원문과 같아야 한다 |
| `category`, `format`, `target_sec`, `tags`, `version` | | 있으면 기록 |
| `generator.feedback_revision` | | 정제 피드백을 반영해 수정한 횟수(0 이상의 정수, 생략 시 0); 2 이상이면 revise를 human_review로 올리며 출력 round는 이 값 + 1이다(`generator.revision`과 별개). |
| 그 밖(`generator`, `self_review`, `status`, `rights_status` …) | | 그대로 출력에 넘긴다. 모르는 키가 있어도 오류가 아니다 |

**2. 관리 사이트 검수 꾸러미** — `검수대상.csv`(`id,title,format,body,source_note`) + `원천목록.csv`(`id,name,grade,credit,url`). 원천 ID의 이용 등급으로 권리를 판정한다(자유 이용 → 허용, 조건부(표시) → 허용+표시 경고, 조건부(동일조건) → 경고, 조건부(건별) → 사람 확인, 사실만 참고 → 원문 베낌 엄격, 허락 전 금지 → reject). 예시 `examples/site-kit/`

**3. 내부 예시 형식** — `examples/samples/`, `examples/existing/`(기존 37편 변환본)

## OUT — 무엇이 나오나

출력 폴더(`--out`)에:

| 파일 | 누가 쓰나 | 내용 |
|---|---|---|
| `revise.jsonl` | **대본 수정 단계** | 꼭 고쳐야 할 항목이 있는 대본마다 한 줄. `{script_id, output_id, round, decision, items: [...], warnings: [...], blocking: [...]}`. **수정 단계는 `items`만 고치면 된다**(`fixable: true`이고 항목 판정이 `revise`인 것). `warnings`는 참고용 경고(뉴스 권리 확인, R2 짧은 사실 표현·세부 조합, 가벼운 듣기 어려움, 길이, 중복 의심 등)라 고치지 않아도 되고, `blocking`은 다시 써도 해결 안 되는 것(권리, Kanana, 상업 배포 위험, 처리 오류)이라 사람이 본다. 꼭 고칠 항목이 없는 대본은 줄이 없다 |
| `site_result.csv` | **관리 사이트 "결과 올리기"** (`--site-kit`일 때) | `id,판정,사유`(UTF-8 BOM). pass·pass_with_flags → 통과, revise → 수정 필요(사유: "N번 문장: 이유 → 고칠 방향" 또는 "대본 전체: 이유 → 고칠 방향", 최대 3개). 방향이 없거나 단계의 일반 안내와 같으면 생략. human_review·reject → 판정 빈칸 + "사람 확인 필요: …". 사이트는 제출(2단계) 대본에만 반영한다 |
| `<content_id>.refine.json` | 디버깅·기록 | `pipeline_version: refine-v1.0.5`, 판정, 단계별 항목, 단계별 원응답(`raw`, 호출된 R2-check·R3-a-check 포함), 재미 점수, 호출 기록, 입력에서 넘겨받은 메타데이터 |
| `summary.csv` | 한눈에 보기 | `content_id, decision, fun_score` |
| `cost.json` | 비용 | 토큰·달러 |

**수정 항목 한 개** (`items[]`·`warnings[]`·`blocking[]`, refine.json도 같은 모양)

```json
{"stage": "R5a", "scope": "sentence", "sentence": 3, "sent": 3, "fixable": true,
 "problem": "규칙: 숫자·영문은 한글 읽기로", "reason": "…",
 "direction": "숫자와 영문을 한글 읽기로 바꾸기", "fix": "(direction과 같음)"}
```

- `sentence`(=`sent`): 1부터 센 문장 번호. 대본 전체에 대한 지적이면 `null`이고 `scope`가 `"script"`
- `direction`(=`fix`): 구체적인 수정 방향. `problem`·`reason`·`direction`·`sentence`는 생성기 검토표 키와 같다
- `fixable`: 다시 써서 고칠 수 있으면 `true`

**판정 5종** (우선순위 순): `reject` > `human_review` > `revise`(`generator.feedback_revision >= 2`이면 human_review) > `pass_with_flags`(경고만) > `pass`

**단계 이름** (`items[].stage`): `R0` 입력·처리 오류 · `R1` 권리 · `R2` 원문 베낌·원천 고유 정보 · `R3-a` 숫자·날짜(한글 수사 포함) · `R3` 주장과 근거 · `R4` 실명·운전 위험·유해·검수 지시 문장 · `R4-a` Kanana 유해 분류 · `R4c` 상업 배포 위험 · `R5a` 듣기 품질 · `R5b` 재미(기록만, 판정에 안 씀) · `R6` 중복

| 단계 | 걸리면 |
|---|---|
| R1 권리 미확인·조건부(건별) | human_review. 허락 전 금지는 reject. 아래 뉴스 원문 인계는 경고 |
| R1 사실해설형 + `user_summary` + 비어 있지 않은 `original_sources` | 경고: 원기사 사실 이용(팀 방침), 매체 표기 확인. `attribution`이 없으면 출처 표시 경고도 추가. `site_grade`가 있으면 사이트 이용 등급 우선 |
| R2 원문 겹침 규칙 | 연속 10자 이상이면 후보. `facts-only`는 revise, 그 밖에 PD·BY-SA·CC0 또는 경제 카테고리는 경고, 나머지는 revise. 원문낭독 제외. 사실해설의 revise 후보만 R2-check로 재판정 |
| R2-check 사실해설 원문 겹침 | `official_name` 제외, `short_fact` 경고, `copied_expression` revise 유지. 다른 제작 유형은 규칙 결과 유지 |
| R2-llm 창작·각색의 원천 고유 정보 | `title_hook`·`money`·`identifying_name`은 revise. `specific_combination`·`incidental_number`는 경고. 규칙의 `must_not_use` 잔존은 revise |
| R3-a 사실 카드·원문과 다른 숫자·날짜 후보 | R3-a-check에서 같은 수량·수량 아님이면 제외. 근거 없음·모순이면 기존 판정 유지(수치: 창작·각색은 경고, 나머지는 revise; 날짜 불일치는 revise) |
| R3 근거와 어긋남·조건 빼고 단정·근거 없음·오래된 정보 | `contradicted` 또는 `major`는 revise, 그 밖의 `minor`는 경고. 본문 근거 미제공이면 경고가 기본이며, 출처 메타데이터를 직접 대조한 모순·과장은 수정 가능 |
| R4 일반인 실명·연락처, 운전 중 "지금" 하라는 화면·조작·필기·눈 감기, 대본 속 검수자에게 하는 지시 | revise |
| R4 실존 회사·인물 비방, 문맥상 유해 표현 / R4-a Kanana UNSAFE | human_review. R4 유해 단어 일치는 LLM 확인 전 후보 |
| R4c 투자 권유·건강 효능 단정·상품 홍보·가사 인용·근거 없는 실존 인물 일화·정치·종교 편향·창작을 실화로 명시 | human_review. 실존 인물 발언·일화는 전달된 원문 근거도 확인 |
| R5a 아라비아 숫자·영문, 괄호, 긴 문장(한글 43음절 초과), 숫자 3개 이상, 첫 청취에 놓칠 문장 | revise |
| 길이 250~450자 밖 | 경고(pass_with_flags) |
| R6 이미 통과한 대본과 내용이 겹침(KURE 임베딩 유사도 .77 이상 / .95 이상) | 경고 / human_review. 같은 관리번호의 새 버전은 중복으로 보지 않음 |

R1 뉴스 원문 인계 분기는 일반 라이선스 검사보다 먼저 적용되며 `unknown`에만 한정되지 않는다. 다른 원천은 기존 권리 검사를 유지한다.

R2-check는 사실해설형에서 규칙의 원문 겹침만으로 구성된 revise 항목을 검사한다. 문장별 후보가 모두 명칭이면 제외, 옮긴 표현 없이 짧은 사실 표현이 섞이면 경고, 옮긴 표현이 하나라도 있으면 revise를 유지한다. 후보 응답 누락·중복 또는 호출 실패는 기존 판정을 유지한다. 결과 항목의 stage는 `R2`다. 창작·각색의 R2-llm은 별도이며, `specific_combination`은 프롬프트의 필수 수정 문구와 달리 코드 정책상 경고다.

R3-a-check는 근거에 없는 수치·근거 날짜 불일치 후보를 원문과 대조한다. 다른 언어·한글 수사·단위 변환도 같은 수량이면 제외한다. 응답 누락·중복 또는 호출 실패는 해당 후보를 유지한다. 브리핑 날짜보다 이후인 날짜는 이 재판정 대상이 아니다. 고유어 수사 + 월·일은 날짜로 읽지 않으며 유월·시월과 단위 톤을 인식한다. 결과 항목의 stage는 `R3-a`다.

R3-b는 `reference_date`를 상대 날짜 이해에만 참고하고 대본에 없는 연도·날짜를 덧붙이지 않는다. 주장 추출 문장과 R3-d의 주장 문맥에 파서가 읽은 `numeric_readings`를 함께 전달한다.

R4는 유해 단어·운전 정규식 일치를 후보로 다룬다. R4 LLM 응답이 있으면 이 규칙 항목을 제거하고 문맥 판정만 남긴다. R4를 건너뛰거나 응답 전에 실패하면 규칙 결과를 유지한다. R4c에는 원문당 최대 60,000자를 전달하고 초과 시 `truncated: true`를 붙인다. 원문은 실존 인물 발언·일화의 근거 여부에만 쓰며 투자·건강·홍보·가사·정치·종교 위험을 면제하지 않는다. 1인칭·실화풍 서술만으로 창작을 실화로 소개했다고 보지 않는다.

## 실행

- 프롬프트 캐싱은 기본으로 켜진다(`--no-prompt-cache`로 끄기).
- `--api-mode batch`는 반값 Message Batches를 쓴다. 결과는 수 분~수 시간 걸릴 수 있으며 기본은 꺼짐.

- `--workers N`으로 대본 병렬 처리 수를 정한다(기본 8, 순차 실행은 `--workers 1`).

레포 루트에서:

```bash
pip install -r ai/refine/requirements.txt
export PYTHONPATH=ai/refine/src

# 대본 생성기 출력 폴더 (*.json)
python -m teum_refine run <대본 JSON 폴더> --out <출력 폴더> --kanana-url http://127.0.0.1:8101

# 관리 사이트 꾸러미 → site_result.csv
python -m teum_refine run --site-kit ai/refine/examples/site-kit --out <출력 폴더>

# 중복 검사 색인: 통과한 대본을 쌓아 두고 다음 배치와 비교 (없으면 새로 만듦)
python -m teum_refine run <대본 JSON 폴더> --out <출력 폴더> --dedup-index <색인.jsonl>

# 규칙만 (API·GPU 없이)
python -m teum_refine run ai/refine/examples/samples --out /tmp/refine --skip-llm --skip-safety --skip-dedup

# 테스트
python -m unittest discover -s ai/refine/tests
```

- 판정 LLM: `claude-haiku-5-5`(effort medium). 지시는 system, 대본·원문은 XML 태그로 감싼 데이터로만 넘긴다(`--prompt-style xml-ko`, 비교용 `v0`). 프롬프트 전문은 `PROMPTS.md`
- API 키: `~/.config/secrets/ClaudePlatform.key`(또는 `REFINE_ANTHROPIC_KEY_FILE`)에서 프로세스 안에서만 읽는다. 레포에 넣지 않는다
- Kanana Safeguard: GPU 서버에서 vLLM으로 띄운다(BF16 약 16GB). `--skip-safety`면 건너뛴다
- 같은 입력은 `<out>/cache/`에서 다시 쓰고 API를 다시 부르지 않는다. 한 편에서 오류가 나도 배치는 계속되고 그 편은 R0 human_review로 남는다
- 중복 검사(R6)는 `nlpai-lab/KURE-v1`을 CPU로 돌린다(첫 실행 때 약 2.3GB 내려받음). `--skip-dedup`이면 건너뛴다
- 지출 상한: 한 실행(`--out`)의 캐시 누적이 `REFINE_TOTAL_BUDGET_USD`(기본 30$)를 넘으면 호출을 막는다. 전체 지출은 API 콘솔 한도로 관리한다
- 비용·시간(v1.0.4 측정): 대본 한 편 약 0.006$, 105편 약 12분(8편 동시, 프롬프트 캐싱, 실제 생성기 출력 105편 기준)

## 측정 — 실제 대본 (11/13, v1.0.5)

생성기 실제 출력 105편의 감사 라벨 기준. v1.0.2의 수정 지적 248건을 맞음 40 · 애매 64 · 오탐 144로 나눈 라벨과 비교한다. 기준선 `08a8c29`도 v1.0.5이며, 최종 `9964741`은 뉴스 대응을 포함한다.

| | 기준선 `08a8c29` | 최종 `9964741` |
|---|---|---|
| 틀린 지적 해소 | 130/144 (90%) | 136/144 (94%) |
| 맞는 지적 유지 | 34/40 (85%) | 34/40 (85%) |
| 기준선 대비 판정 5종 일치 | — | 91/105 |
| 기준선 대비 통과·막힘 일치 | — | 92/105 |

뉴스 12편: **통과(경고) 4 · 수정 요청 6 · 사람 확인 2**(사람 확인 2편은 수정 횟수 초과). 권리 검사로 사람 확인이 된 대본은 0편. 남은 막는 지적은 기사 표현 겹침(4편), 듣기 어려움, 재개발 동의 조건을 잘못 옮긴 주장 1건, 공직자 실체 식별 1건이다.

## 측정 — 결함 오류셋 (11/13, v1.0.5)

1~6판 합계. 검출·오탐은 통과·경고를 벗어나 막힌 판정 기준이다.

| | 기준선 `08a8c29` | 최종 `9964741` |
|---|---|---|
| 결함 검출 | 131/142 | 133/142 |
| 원본 대본 오탐 | 12/44 | 11/44 |

기준선의 호출 제한(429) 오염 사례는 양쪽에서 같이 제외했다. 최종 측정은 305건 중 429 오염 0건. 감사 라벨·오류셋 수치는 해당 표본의 결과이며 전체 배포 품질로 일반화하지 않는다.

## 측정 — 재실행 변동 (11/11, v1.0.5 초기본)

같은 코드·프롬프트로 105편을 두 번 판정했을 때 5종 판정 일치는 **94/105**, 통과·막힘 일치는 **95/105**였다(Kanana·중복 검사 제외). 이전 실행의 429 처리 오류 2편이 포함된 비교다. 통과·막힘이 바뀐 10편은 R3 6편(1편은 R4도 해당), R5a 2편, 처리 오류 2편이었다.

개별 대본의 판정은 재실행으로 바뀔 수 있으므로 작은 차이를 개선·악화로 단정하지 않는다. 뉴스 날짜 오탐도 수정 전 4회 중 1회, 수정 뒤 4회 중 0회인 작은 표본만 확인됐다.

## 아직 없는 것

- 원천 문장을 뜻만 유지해 바꿔 쓴 베낌의 포괄적 검사(R2 규칙은 문자 일치, 사실해설 R2-check는 잡힌 후보만 재판정)
- R3(주장 대조)는 아직 가장 약하다. 판정과 이유가 어긋나는 지적이 가끔 남는다
- 창작 각색이 원문을 문장 단위로 따라 쓴 경우는 일부만 잡는다
- 뉴스 공직자 실명을 R4가 수정 요청으로 잡은 사례가 남아 있다
- 재미(R5b)는 사람 판단과 맞지 않아 판정·순위에 쓰지 않는다
- 중복 검사는 완전히 새로 쓴 중복 일부를 놓친다
