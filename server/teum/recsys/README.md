# 추천 틀 (recsys) · S2-09

운영 서버 안에서 콘텐츠 제작 파이프라인과 따로 도는 추천 단계다. 규칙 추천(`playlist/`)을 기준선으로 두고, 그 위에 전략을 바꿔 끼운다. 기존 `GET /api/v1/playlist`는 바꾸지 않았다.

## 방법론: 데이터가 쌓이는 순서대로 3단계

| 단계 | 전략 | 쓰는 신호 | 언제 |
| --- | --- | --- | --- |
| 1 | `rule` 규칙 기반 | 내 관심사, 내 좋아요·보관·완청, 다양성 규칙 | 지금 기본값 |
| 2 | `bandit` Thompson sampling | 1 + 다른 사용자들의 완청·넘김 | 사용자가 생기기 시작할 때 |
| 3 | `ease` EASE 협업 필터링 | 2 + "이걸 좋아한 사람은 저것도" | 사용자 20명·좋은 반응 100건 넘으면 |

- **bandit:** 콘텐츠마다 Beta(α+성공, β+실패)에서 값 θ를 하나 뽑아 (θ−0.5)×2를 규칙 점수에 더한다. 성공은 완청이나 좋아요이고, 실패는 완청 없이 넘긴 것이다. 기록이 적은 콘텐츠는 분포가 넓어 가끔 위로 올라오고(탐색), 기록이 쌓이면 잘 듣는 콘텐츠가 올라온다(활용). 출발값 α·β는 같은 카테고리 다른 콘텐츠의 성공률을 가상 관측 4번만큼만 반영한다(자기 기록은 두 번 세지 않는다). 그래서 새 콘텐츠도 카테고리 수준에서 출발한다. Spotify 홈(BaRT, McInerney et al., RecSys 2018)과 팟캐스트 cold start(Impatient Bandits, 2023)에서 쓰는 방식이다.
- **ease:** Steck, "Embarrassingly Shallow Autoencoders for Sparse Data"(WWW 2019)의 닫힌 해다. 새벽 일괄 작업에서 학습하고, 서버는 결과 JSON만 읽는다. 이야기 200편, 사용자 2,000명 기준 학습 0.9초(이 서버 CPU, 순수 파이썬)다.
- **뺀 것:** NCF·GNN은 넣지 않는다. 튜닝한 단순 모델이 NCF를 이기고(Rendle et al., RecSys 2020), 신경망 추천 18편 중 재현된 7편 중 6편이 단순 기법에 졌다(Dacrema et al., RecSys 2019). 콘텐츠 수백 편과 초기 사용자 규모에서는 근거가 없다.
- **같은 규칙 유지:** 전략 가산점을 더한 뒤에도 규칙 추천의 다양성 조건을 같은 함수(`choose_index`)로 다시 적용한다. 연속 5개 중 같은 대분류 최대 3개, 같은 카테고리 연속 금지, 선택 대분류 첫 자리 보장이다. 시간 편성(광고 10~15%, 대분류 하한)도 같은 `plan()`을 쓴다.

## 구조

```
앱 ──GET /recsys/playlist──▶ router.py ── assign.py (전략 고르기: algo > 비율 나눔 > 기본값)
                                 │
                                 ▼
                            service.compose()
                 ① playlist.ranker.rank_candidates  (규칙 점수, playlist 코드 그대로)
                 ② strategies.rerank                 (전략 가산점 + 다양성 재적용)
                 ③ playlist.planner.plan             (남은 시간·광고 편성, 그대로)
                 ④ serve_log.write                   (무엇을 보여 줬는지 기록)
                                 ▲
        signals.py (청취 기록 집계, 60초 캐시)   ease.py (새벽 학습 결과 JSON)
```

| 파일 | 하는 일 |
| --- | --- |
| `router.py` | `GET /recsys/playlist`, `GET /recsys/status` |
| `assign.py` | 사용자별 전략 고르기. 비교 실험용 사용자 단위 나눔 |
| `strategies.py` | rule·bandit·ease 가산점과 다양성 재적용 |
| `signals.py` | 청취 기록(listen_state) 읽기 전용 집계 |
| `serve_log.py` | 노출 기록 (`recsys_serve.sqlite`, 청취 DB와 따로) |
| `ease.py` | EASE 학습·점수·저장 |
| `train.py` | 새벽 일괄 학습 CLI |
| `evaluate.py` | 전략별 성적표 CLI |
| `simulate.py` | 가상 사용자 모의 실험 CLI (실제 데이터 아님) |

## 쓰는 법

```http
GET /api/v1/recsys/playlist?user_id=u1&minutes=30                 # 기본 전략(rule)
GET /api/v1/recsys/playlist?user_id=u1&remain_sec=1260&algo=bandit # 남은 주행 21분, bandit
GET /api/v1/recsys/status
```

- 입력은 `/playlist`와 같다(`user_id, minutes, interests, speed, mode`). 여기에 아래 값이 더해진다.
  - `algo`: `rule`·`bandit`·`ease` 중 하나. 생략하면 서버 설정을 따른다.
  - `remain_sec`: Pleos NaviHelper `getDestinationInfo().remainTime`(초)이다. `minutes`가 없으면 이 값으로 편성 시간을 정한다.
  - `passengers`, `hour`: 지금은 기록만 하고 점수에 쓰지 않는다.
- 응답은 `/playlist`와 같은 모양이다. 여기에 아래 값이 더해진다.
  - `recsys`: 실제로 쓴 전략, 요청한 전략, 대체 사유, 배정 방식, request_id, 맥락 값.
  - 이야기마다 `rule_score`와 `recsys.bonus`가 붙는다.
  - 노출 기록 저장이 실패해도 목록은 나가고, `recsys.log_error`에 이유가 남는다.
- 서버 설정은 환경변수로 한다.
  - `TEUM_RECSYS_DEFAULT`: 기본 전략. 기본값은 `rule`이다.
  - `TEUM_RECSYS_SPLIT`: 사용자를 나눠 비교 실험을 한다. 예: `rule:50,bandit:50`. 같은 사용자는 늘 같은 쪽에 배정된다.
- 저장 파일은 청취 DB(`TEUM_EVENTS_DB`)와 같은 폴더에 생긴다. 노출 기록은 `recsys_serve.sqlite`, EASE 모델은 `recsys_ease.json`이다.

```bash
cd server
python -m teum.recsys.train --events $TEUM_EVENTS_DB            # 새벽 일괄 (cron)
python -m teum.recsys.evaluate --events $TEUM_EVENTS_DB         # 전략별 완청률·넘김률
python -m teum.recsys.simulate --data-root $TEUM_DATA_ROOT      # 가상 사용자 비교
```

## 모의 실험 (가상 사용자, 절차 점검용)

`simulate.py`를 이야기 38편, 사용자 300명, 6회차, 시드 5개로 돌렸다. 가정은 이렇다. 사용자마다 좋아하는 카테고리가 1~2개 있다. 콘텐츠마다 모두에게 통하는 완성도 차이가 있다. 사용자의 절반만 관심사를 직접 고른다.

| 전략 | 완청률 (평균 ± 표준편차) |
| --- | --- |
| rule | 0.267 ± 0.016 |
| bandit | 0.321 ± 0.009 |
| ease | 0.314 ± 0.011 |

가정을 바꾸면 숫자도 바뀐다. 실제 비교는 사용자가 생긴 뒤 `TEUM_RECSYS_SPLIT`과 `evaluate.py`로 한다.

## 아직 안 한 것

- 선택 이력·넘김 시점 신호 연결(S2-10)은 `/playlist`와 같이 `not_connected`다.
- 차량 맥락(동승자·시간대)을 점수에 쓰는 일은 앱이 값을 보내기 시작한 뒤에 한다(S2-11).
- `recommend()`에 전략 끼울 자리가 없어 편성 조립 부분을 `service.py`에 한 번 더 썼다. `playlist`의 `recommend()`에 `rerank` 인자 하나가 생기면 지울 수 있다.
