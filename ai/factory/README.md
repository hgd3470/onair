# 자동 제작 루프 (teum_factory)

소재가 입력함에 들어오면 대본 생성, 검수, 수정 왕복을 사람 없이 진행하고, 켜 두면 게시까지 한다. 생성기(`ai/script_agent`)와 검수기(`ai/refine`)의 코드는 고치지 않고, 두 프로그램의 명령과 제작 관리 사이트(`services/studio`)의 화면 뒤 API만 순서대로 부른다. 그래서 사람이 사이트에서 하던 일과 같은 기록이 남는다. 단계마다 쓰는 모델은 설정 한 줄로 고른다.

## 한 바퀴에 하는 일

| 단계 | 하는 일 | 부르는 것 |
|---|---|---|
| ① 수집 | 소재 md가 입력함(`TEUM_HOME/script_agent/input/`)에 들어온다 | `TEUM_COLLECT_CMD`를 `TEUM_COLLECT_INTERVAL`(기본 하루)마다 (없으면 사람이 넣은 것만). `ai/source_collector`는 `python -m teum_factory.run_collector`로 연결한다 |
| ② 생성 | 소재 → 대본 JSON. 생성기가 끝내지 못한 작업(`processing/`, 끊긴 배치 포함)이 있으면 새 소재가 없어도 불러 이어 가게 한다 | `script-agent run --limit N` (`run_script_agent.py`가 감싸서 부른다) |
| 제출 | 새 대본을 사이트에 올리고 "제출"로 넘긴다. `output/` 바로 아래와 `output/<배치>/`, `output/_unbatched/`를 다 본다 | 사이트 올리기 |
| ③ 검수 | 검수 대기 대본을 받아 검사하고 판정을 사이트에 올린다 | `python -m teum_refine run` (`run_refine.py`가 감싸서 부른다) |
| 수정 | "수정 필요"가 나온 자동 생성 대본을 고쳐 다시 제출한다 | `script-agent revise` |
| ④ 음성 | 통과한 대본에 목소리를 배정해 합성하고 받아쓰기로 대조한다 | `TEUM_AUTO_VOICE=1`이면 사이트가 자동으로 한다. 기본은 꺼짐(사람이 음성 만들기를 누른다) |
| ⑤ 게시 | 기본은 사람이 듣고 승인한다. `TEUM_AUTO_PUBLISH=1`이면 받아쓰기 대조를 통과한 음원을 바로 카탈로그에 게시한다 | 사람 또는 사이트의 "앱에 추가" |

검수와 수정은 한 바퀴 안에서 판정이 날 때까지 되풀이한다(수정 2회까지).

이슈 수집기의 앞단 명령은 `ai/issue_collector/collect_to_inbox.sh --limit N --inbox "{inbox}"`다. `N`은 시사·국내 시장을 합친 소재 상한이며, 5이면 시사 2편·시장 3편을 수집해 전달한다. 설치·키 이름·`TEUM_COLLECT_CMD` 예시·중복 방지·30분 실행 제한은 [수집기 연결 계약](../issue_collector/tools/INTEGRATION.md)을 참조한다.

## 단계별 모델

`TEUM_HOME/teum.env`의 한 줄이 한 단계다. 값을 바꾸고 다시 띄우면 그 단계만 다른 모델로 돈다.

| 줄 | 단계 | 값 |
|---|---|---|
| `TEUM_GENERATOR` | ② 대본 작성 (분석, 초안, 수정) | `local`, `gemini[:모델]`, `openai[:모델]` |
| `TEUM_REVIEWER` | ② 생성기 자체 검토 | `local`, `gemini[:모델]`, `openai[:모델]` |
| `TEUM_REFINE` | ③ 검수 | `local`, `claude[:모델]` |

줄을 비우면 저장소 기본값(생성 Gemini, 자체 검토 GPT, 검수 Claude)을 쓴다. 생성기의 실행 방식은 생성기 저장소 설정(지금은 Gemini 배치)을 따르고, `TEUM_SCRIPT_AGENT_MODE=standard|batch`로 바꿀 수 있다. `local`을 고른 단계가 있으면 `standard`로 부른다.

배치는 결과가 몇 시간 뒤에 올 수 있어 단계별 시간 제한을 넉넉히 둔다: 수집 `TEUM_COLLECT_TIMEOUT`(기본 3시간), 생성 `TEUM_GENERATE_TIMEOUT`(6시간), 수정 `TEUM_REVISE_TIMEOUT`(3시간). 시간 안에 못 끝내도 생성기는 다음 바퀴에 같은 배치를 이어 받는다. 수집기가 배치 대기(종료 코드 2, `batch_pending`)로 끝나면 하루를 기다리지 않고 `TEUM_COLLECT_RETRY`(기본 30분) 뒤에 같은 날짜로 다시 불러 이어 받는다. `local`은 `TEUM_LLM_URL`의 OpenAI 호환 서버를 부르고, 모델 이름은 `TEUM_LLM_MODEL`(비우면 서버의 첫 모델)이다. `local`일 때의 동작은 다음과 같다.

- 답은 JSON 스키마로 형식을 강제해 받는다. 모델이 빈 줄만 계속 내면 끝까지 기다리지 않고 끊어 다시 묻고(세 번까지), 그래도 안 되면 미완성 답으로 처리해 그 대본을 사람 확인으로 넘긴다. 끝까지 나오지 않은 요청은 `TEUM_HOME/factory/llm_trouble/`에 남는다.
- 소재가 `TEUM_LLM_MAX_SOURCE_CHARS`(기본 6,000자)보다 길면 뒤를 줄여 넣고 원본을 `factory/source_originals/`에 남긴다.
- 검수 판정 사유에 검수한 모델 이름이 적힌다.

모델을 고르는 코드는 `src/teum_factory/models.py` 한 곳에 있다. `run_script_agent.py`와 `run_refine.py`는 생성기와 검수기를 그대로 실행하되, `local`을 고른 단계의 모델 호출만 `models.py`로 돌린다.

## 사람 없이 게시하기

`TEUM_AUTO_PUBLISH=1`로 두면 자동 생성한 대본이 ①에서 ⑤까지 사람 손을 거치지 않는다. `all`이면 사람이 올린 대본도 포함한다. 게시 전에 음원을 받아써서 대본과 대조하고, 다음 경우에는 게시하지 않는다.

| 경우 | 처리 |
|---|---|
| 받아쓰기가 대본과 3% 넘게 다르다 | 게시하지 않고 댓글을 남긴다. 사람이 듣고 판정한다 |
| 받아쓰기 대조가 아직 안 됐다 | 다음 바퀴에 다시 본다. 받아쓰기는 `TEUM_STT_PYTHON`에 torch가 깔린 파이썬을 줘야 돈다 |
| LLM 검사나 안전 검사를 건너뛴 채 통과했다 (`TEUM_REFINE_RULES_ONLY=1`, 또는 `TEUM_KANANA_URL`이 비어 있음) | 게시하지 않는다. 사람이 듣고 게시한다 |
| 생성기가 `release_status`를 적었다 (`pending_content_review`, `pending_processing_review`, `pending_editorial_approval`, `blocked`) | 게시하지 않는다. 사이트의 "앱에 추가"도 같은 대본을 거부한다 |
| 분류가 `TEUM_AUTO_PUBLISH_HOLD_TOPICS`에 들어 있다 (기본값 `C16,C08`: 뉴스·사회·시사, 돈·소비·생활경제) | 게시하지 않는다. 사람이 듣고 게시한다 |

자동 게시는 변경 기록에 "자동: 받아쓰기 대조 통과, 사람 청취 없이 자동 게시"로 남아 사람이 듣고 올린 것과 구분된다. 지금 설정에서 무엇이 자동 게시에서 빠지는지는 `preflight`의 "⑤ 자동 게시 제외"에 나온다.

## 검수 비용

검수기의 예산(`REFINE_TOTAL_BUDGET_USD`)은 실행 폴더 하나 안에서만 센다. 루프는 바퀴마다 새 실행 폴더를 만들므로 이 값은 한 바퀴의 상한이고, 하루나 한 달의 상한이 되지 못한다. 그래서 루프는 `runs/*/out_*/cost.json`을 날짜별로 합쳐 `status`의 "검수 비용(USD)"과 `log.jsonl`의 `refine_cost`에 남긴다. 오늘 합계가 `TEUM_REFINE_DAILY_WARN_USD`(기본 5)를 넘으면 같은 자리에 경고 한 줄이 붙는다. 경고는 검수를 멈추지 않는다. 지출 상한은 API 키 콘솔의 지출 한도로 건다.

## 판정별 처리

| 검수기 판정 | 사이트에 남는 것 | 다음 |
|---|---|---|
| 통과, 경고 통과 | 판정 "통과" | ④ 음성 합성 |
| 수정 요청 | 판정 "수정 필요"와 사유 | 자동 생성 대본은 생성기가 고쳐 다시 제출. 사람이 올린 대본은 올린 사람이 고친다 |
| 사람 확인, 폐기 | 판정 없이 사유에 "사람 확인 필요" | 멈춘다. 사람이 보고 판정한다 |
| 수정 2회 뒤에도 수정 요청 | 사유에 "사람 확인 필요" | 멈춘다 |
| 생성기가 스스로 보류(hold) | 제출하지 않고 댓글 | 멈춘다 |
| 자동 수정본을 생성기가 보류(hold) | 최신 수정본을 올리고 사유에 "사람 확인 필요" | 자동 검수와 음성 합성으로 넘기지 않는다. 사람이 최신본을 보고 판정한다 |

자동 판정의 사유는 `[자동 검수]`로 시작한다. 건너뛴 검사가 있으면 `[자동 검수 · 안전 검사 제외]`처럼 사유에 드러난다. 안전 검사는 `TEUM_KANANA_URL`에 Kanana 모델 서버 주소를 줘야 돌고, 중복 검사는 `sentence-transformers`가 깔려 있어야 돈다.

같은 판을 두 번 검수하지 않는다. 대본 내용이 바뀌면 다시 검수한다.

## 명령

```bash
export PYTHONPATH=ai/factory/src
python -m teum_factory preflight        # 단계별 모델, 키, 사이트 연결, 입력함
python -m teum_factory tick --dry-run   # 무엇을 할지만
python -m teum_factory tick             # 한 바퀴
python -m teum_factory status           # 단계별 편수, 사람 확인으로 넘긴 대본
python -m teum_factory loop             # TEUM_FACTORY=1일 때 주기적으로 (teum_up.py가 띄운다)
```

기록은 `TEUM_HOME/factory/`에 남는다. `state.json`은 대본마다 올린 판, 검수한 판, 수정 횟수를 담고, `log.jsonl`은 바퀴마다의 결과를, `runs/<시각>/`은 검수기 입력과 출력을 담는다.

## 테스트

```bash
PYTHONPATH=ai/factory/src python -m pytest ai/factory/tests -q
```

`test_factory_e2e.py`는 실제 사이트 프로세스를 임시 폴더에 띄워 소재 두 편을 끝까지 보낸다. 생성기는 `tests/fake_script_agent.py`가 대신하고, 검수는 규칙 검사만 한다. `test_models_local.py`는 가짜 모델 서버를 띄워 단계별 모델 고르기와 `local` 호출을 확인한다. 실제 모델과 TTS는 부르지 않는다.
