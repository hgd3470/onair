# listening 모듈 규격 — 사용자·반응 API 설계안 (S1-02) v0.21

## 이벤트

### 종류

| type | 보내는 시점 | position_sec |
|---|---|---|
| play | 콘텐츠 재생 시작. playback_id를 새로 만든다 | 시작 위치 |
| position | 10초마다. 탐색 직전·직후, 배속 변경 시, 일시정지·건너뛰기·전환 직전 | 현재 위치 |
| pause | 사용자 일시정지, 안내가 아닌 오디오 포커스 상실(시동 OFF·전화). 내비 안내 중이어도 보낸다. 안내 때문에 앱이 내부적으로 멈추는 것은 duck_start로만 보낸다 | 현재 위치 |
| resume | 일시정지에서 실제 재개. 안내 중 정지했다면 안내가 끝난 뒤 다시 틀 때 | 현재 위치 |
| skip | 다음 콘텐츠로 넘김 | 넘긴 시점 위치 |
| complete | 끝까지 재생돼 자동 전환 | 콘텐츠 길이 |
| duck_start | 내비 안내 시작 | 현재 위치 |
| duck_end | 내비 안내 종료. 재생이 재개됐는지와 무관하게 보낸다 | 현재 위치(Android는 되감은 위치) |
| like, unlike | 좋아요, 취소 | 없음 |
| save, unsave | 저장, 취소 | 없음 |
| ad_start, ad_complete, ad_error | 광고 시작, 정상 종료, 실패 | 0, 광고 길이, 실패 위치 |

데모 기획의 play_start는 play, play_complete는 complete에 해당한다. 나머지는 같은 이름이다.

### 필드

| 필드 | 값 | 만드는 쪽 |
|---|---|---|
| event_id | UUID. 재전송해도 같은 값 | 앱 |
| content_id | catalog의 content_id. 광고는 광고 ID | 앱 |
| content_version | 재생 중인 음원의 catalog version. 위치가 실리는 콘텐츠 이벤트에 포함. 생략 시 1 | 앱 |
| playback_id | UUID. play마다 새로 만들고 그 재생의 이벤트에 붙인다. 재생 중이 아닌 like·save는 비운다 | 앱 |
| seq | 기기 안에서 1씩 증가하는 정수. 앱을 다시 켜도 이어진다 | 앱 |
| type | 위 표의 값 | 앱 |
| position_sec | 재생 위치(초). like·save에는 없다 | 앱 |
| playback_rate | 재생 속도. 위치가 실리는 콘텐츠 이벤트에 포함. 생략 시 1.0 | 앱 |
| client_ts | 기기에서 일어난 시각. ISO 8601, 시간대 포함 | 앱 |
| user_id | 토큰으로 결정 | 서버 |
| received_at | 서버 수신 시각 | 서버 |

검증: position_sec는 값이 있으면 0 이상, seq는 0 이상, playback_rate는 0 초과 3 이하이다.
play·position·pause·resume·skip·complete·duck_start·duck_end에는 빈 문자열이 아닌 playback_id와 position_sec가 필수이다.

ad_* 이벤트에는 content_version과 playback_rate가 없다.

### 오프라인 큐

- 통신이 끊기면 이벤트를 로컬 큐에 쌓고, 연결되면 POST /events/batch로 최대 500개씩 보낸다.
- 큐 상한은 1,000개. 넘치면 오래된 것부터 이 순서로 버린다.
  1. 10초 주기 position. 탐색 직전·직후, 배속 변경, 정지 직전에 보낸 position은 남긴다
  2. 가장 오래된 playback_id의 이벤트. 그 안의 like·save는 남긴다
  3. 같은 콘텐츠의 like·save 중 최신 하나를 뺀 나머지
  4. 가장 오래된 like·save
- 시동 OFF: 오디오 포커스를 잃는 순간 pause를 큐에 넣고 다음 연결 때 보낸다. 재시동 시 자동 재생은 하지 않는다.

## listen_state

서버가 사용자·콘텐츠마다 하나씩 유지하는 값. 이벤트가 저장될 때마다(배치는 사용자·콘텐츠별로 한 번) 그 사용자·콘텐츠의 저장된 이벤트 전체를 seq 순으로 다시 접어 덮어쓴다. 늦게 도착한 이벤트도 seq 자리에 들어가므로, 같은 이벤트 집합이면 도착 순서와 상관없이 같은 값이 나온다. GET /history로 돌려준다.

### 필드

| 필드 | 값 | 갱신 |
|---|---|---|
| last_position_sec | 위치가 실린 마지막 이벤트의 position_sec | like·save는 갱신하지 않음 |
| played_sec | played 구간의 합. 겹치는 구간은 한 번만 더함 | |
| completed | played_sec ≥ 0.8 × 콘텐츠 길이, 또는 prev_completed | 과거 완청 여부. 다시 들어도 바뀌지 않음 |
| finished | 현재 재생(위치가 실린 이벤트 중 seq가 가장 큰 것의 playback_id) 안에서 complete 수신, 또는 그 안의 마지막 위치 ≥ 콘텐츠 길이 − 1초 | 현재 재생의 끝 도달 여부. 새 play가 오면 false |
| skipped | 현재 버전 묶음 안에서 skip을 한 번이라도 수신 | |
| liked, saved | like/unlike, save/unsave 중 seq가 가장 큰 이벤트의 상태 | |
| last_ts | 위치가 실린 마지막 이벤트의 client_ts. epoch 초, 숫자 | like·save는 갱신하지 않음 |
| content_version, duration_at_version | 마지막 위치 이벤트의 version과 현재 버전 묶음의 콘텐츠 길이 | |
| base_version, prev_completed | 지금 접고 있는 버전 묶음의 시작 version과, 그 전 묶음의 completed | 길이가 1초 넘게 다른 version이 올 때만 바뀜 |

playlist는 completed가 true인 콘텐츠를 뺀다. intent는 finished가 false이고 last_position_sec이 0보다 큰 콘텐츠 중 last_ts가 가장 큰 것을 "이어서 들려줘" 대상으로 고른다. 완청한 콘텐츠를 다시 듣다 멈추면 completed true·finished false라, 편성에서는 빠지고 이어 듣기에는 잡힌다.

### played 구간

같은 playback_id의 이벤트를 seq 순으로 놓고 중단 사유 두 개를 따로 따라간다. 정지와 내비 안내는 서로 독립이라 하나로 합치면 안내 중에 정지한 경우를 놓친다.

- 정지: pause에서 켜지고 resume에서 꺼진다
- 안내: duck_start에서 켜지고 duck_end에서 꺼진다
- play는 둘 다 끈다. skip, complete 이후는 그 재생의 끝이라 구간을 세지 않는다
- 재생 중 = 두 사유가 모두 꺼진 상태. duck_end가 와도 정지가 켜져 있으면 resume까지 정지
- position은 사유를 바꾸지 않는다. like·save는 건너뛴다

재생 중에 위치가 실린 이웃한 두 이벤트 A→B가 다음 둘을 만족하면 [A.position, B.position]을 played 구간으로 인정한다.

- B.position − A.position > 0
- |(B.position − A.position) − A.playback_rate × (B.client_ts − A.client_ts)| ≤ 1초

played_sec은 모든 playback_id의 played 구간을 합집합으로 합친 길이다.

### content_version이 바뀐 경우

이벤트의 content_version과 listen_state의 base_version·duration_at_version을 비교한다.

| 경우 | 처리 |
|---|---|
| 새 version의 길이 차이가 1초 이내 | content_version만 갱신. 같은 묶음으로 계속 접는다 |
| 길이 차이가 1초를 넘는 새 version의 첫 위치 이벤트 | prev_completed에 지금의 completed를 넣고 base_version을 새 version으로. 그때부터는 새 묶음의 이벤트만 접으므로 played_sec·finished·skipped·last_position_sec은 새로 계산되고, completed·liked·saved는 유지 |
| 새 version 이벤트가 아직 없고 catalog version만 바뀜 | GET /history에서 last_position_sec 0, finished false로 응답. 저장값 유지 |
| content_version이 base_version보다 작은 이벤트 | 저장만 하고 접기에서 뺀다. like·save는 version이 없으므로 항상 반영 |

### 예시

콘텐츠 길이 100초, 보고 주기 10초. 표기는 type@위치/시각, 시각은 재생 시작 기준 초. 따로 적지 않으면 한 행의 이벤트는 같은 playback_id.

| 상황 | 이벤트 | played 구간 | played_sec | completed | finished | last_position_sec |
|---|---|---|---|---|---|---|
| 끝까지 들음 | play@0/0, position@10/10 … @90/90, complete@100/100 | [0,100] | 100 | true | true | 100 |
| 12초에서 85초로 탐색, 추가 position 없음 | play@0/0, position@10/10, position@93/20, complete@100/27 | [0,10] [93,100] | 17 | false | true | 100 |
| 같은 탐색, 직전·직후 position 있음 | play@0/0, position@10/10, position@12/12, position@85/12, position@93/20, complete@100/27 | [0,12] [85,93] [93,100] | 27 | false | true | 100 |
| 1.5배속, playback_rate 1.5 보고 | play@0/0, position@15/10 … @90/60, complete@100/66.7 | [0,100] | 100 | true | true | 100 |
| 1.5배속, playback_rate 없음 | 위와 같음 | 없음 | 0 | false | true | 100 |
| 내비 안내 20초, Android(일시정지 후 2.5초 되감기) | play@0/0, position@10/10, duck_start@15/15, duck_end@12.5/35, position@22.5/45, pause@30/52.5 | [0,15] [12.5,30] | 30 | false | false | 30 |
| 내비 안내 4초, 웹 플레이어(볼륨만 낮춤) | play@0/0, position@10/10, duck_start@15/15, duck_end@19/19, position@30/30, pause@35/35 | [0,15] [19,35] | 31 | false | false | 35 |
| 내비 안내 중 사용자 정지(전화 포함). duck_end 뒤에도 resume까지 정지 | play@0/0, position@10/10, duck_start@15/15, pause@15/18, duck_end@15/35, resume@15/60, position@25/70, pause@30/75 | [0,15] [15,30] | 30 | false | false | 30 |
| 오프라인에서 좋아요 뒤 취소, 배치가 뒤집혀 도착 | position@50/50 seq40, unlike/55 seq42, like/50 seq41, position@60/60 seq43 | [50,60]. liked false | 10 | false | false | 60 |
| 큐 넘침으로 50~90초 position 유실 | play@0/0 … position@50/50, position@90/90, complete@100/100 | [0,100] | 100 | true | true | 100 |
| complete만 도착 | complete@100/100 | 없음 | 0 | false | true | 100 |
| play와 complete만 남음 | play@0/0, complete@100/100 | [0,100] | 100 | true | true | 100 |
| play·complete가 먼저, 중간 pause·resume이 늦게 도착 | play@0/0, pause@40/40, resume@40/100, position@50/110 … @90/150, complete@100/160 | [0,40] [40,100] | 100 | true | true | 100 |
| 정지 중 90초로 탐색, pause·resume이 늦게 도착 | play@0/0, pause@10/10, resume@90/90, complete@100/100 | [0,10] [90,100] | 20 | false | true | 100 |
| 웹 플레이어 안내 25초, duck_start만 다른 배치로 늦게 도착 | play@0/0, position@10/10 … @40/40, duck_start@45/45, duck_end@70/70, position@80/80, @90/90, complete@100/100 | [0,45] [70,100] | 75 | false | true | 100 |
| 완청한 콘텐츠를 다시 듣다 멈춤 (재생 2는 새 playback_id) | 재생 1: play@0/0 … complete@100/100 / 재생 2: play@0/0, position@10/10, @20/20, pause@30/30 | [0,100] | 100 | true | false | 30 |

늦게 도착한 세 행: 첫 행은 play·complete만으로 먼저 접으면 위치 차 100초와 시각 차 160초가 어긋나 구간이 안 잡혀 completed false였다가 나머지가 오면 100·true가 된다. 둘째 행은 정지 중 탐색으로 시각 차가 맞아떨어져 play·complete만으로는 [0,100]·completed true였다가 pause·resume이 오면 20·false로 돌아온다. 셋째 행은 duck_start 없이 먼저 접으면 [0,100]·true였다가 도착 뒤 75·false로 돌아온다. 어느 쪽이든 끝값은 전체 이벤트 집합으로만 정해진다.

## 인증

### device 등록

1. 앱이 첫 실행 때 무작위 install_id를 만든다. 사람·차량과 연결되는 값은 쓰지 않는다.
2. POST /devices로 install_id를 보내면 서버가 user_id와 token을 돌려준다. 둘 다 서버가 만든 무작위 값.
3. 앱은 token을 비공개 저장소에 두고, 이 문서의 API를 부를 때 Authorization: Bearer <token>을 붙인다. 본문·쿼리의 user_id는 받지 않는다.
4. 서버가 모르는 token이면 401로 처리.

### token 규칙

| 상황 | 동작 |
|---|---|
| POST /devices 호출 | 매번 새 user_id·token. 같은 install_id를 다시 보내도 새 사용자 |
| token 보유 중 | /devices를 다시 부르지 않는다. token은 만료되지 않는다 |
| token 분실(저장소 손상, 재설치) | /devices로 새 사용자가 된다. 이전 user_id의 기록은 남지만 연결되지 않는다 |
| 분실 뒤 남은 큐 | 새 token으로 보낸다. 이미 저장된 event_id는 duplicate로 버려진다 |

### playlist·search·intent의 user_id

| 단계 | 앱이 보내는 값 |
|---|---|
| 지금 | 앱이 만든 문자열 user_id. playlist·search는 쿼리, intent는 본문 |
| 이 설계 구현 후 | POST /devices가 돌려준 user_id를 같은 자리에. 세 모듈은 코드 변경 없음 |
| 토큰 전환 후 | user_id를 보내지 않고 token만. 세 모듈이 core/deps.py의 토큰 의존성으로 바꾼 뒤 |

## API

경로 앞에 /api/v1. POST /devices 외에는 Authorization: Bearer <token>.

### POST /devices

```
{"install_id": "3f2a9c…", "app_version": "0.1.0", "platform": "pleos-connect"}

201
{"user_id": "u_7c1e…", "token": "t_9d4b…"}
```

### POST /events

```
{"event_id": "e1…", "content_id": "story-s01", "content_version": 1, "playback_id": "p1…", "seq": 41,
 "type": "position", "position_sec": 38.2, "playback_rate": 1.0, "client_ts": "2026-10-30T09:12:03+09:00"}

200
{"accepted": 1, "duplicate": 0}
```

같은 event_id가 다시 오면 저장하지 않고 {"accepted": 0, "duplicate": 1}. 앱은 둘 다 성공으로 처리한다.

### POST /events/batch

```
{"events": [ {...}, {...} ]}          # 최대 500개

200
{"accepted": 48, "duplicate": 2,
 "rejected": [{"index": 7, "event_id": "e9…", "reason": "unknown_content"},
              {"index": 12, "event_id": null, "reason": "invalid"}]}
```

- 항목 단위로 검사한다. 한 항목이 잘못돼도 나머지는 받는다.
- rejected는 배열 index와 event_id로 식별한다. event_id가 없으면 null.
- reason: unknown_content(공개 중인 catalog에 없는 ID), invalid(필수 필드 누락, 모르는 type).
- 앱은 accepted·duplicate·rejected를 모두 큐에서 지운다.

### GET /history

```
200
{"user_id": "u_7c1e…",
 "items": [
   {"content_id": "story-s01", "last_position_sec": 38.2, "played_sec": 36.9,
    "completed": false, "finished": false, "skipped": false, "liked": true, "saved": false,
    "last_ts": 1793319123.0}
 ]}
```

공개 중인 콘텐츠만 포함한다.

### PUT /me/interests, GET /me/interests

```
PUT
{"interests": ["history", "science", "life"]}      # catalog의 category 값. 전체 교체

200
{"interests": ["history", "science", "life"], "updated_at": 1793319123.0}

GET
200
{"interests": [...], "updated_at": ...}             # 없으면 {"interests": [], "updated_at": null}
```

### 오류

| 코드 | 경우 | 앱 동작 |
|---|---|---|
| 401 | 헤더에 token 없음 | 앱 버그. 큐 보존, 보고 |
| 401 | 서버가 모르는 token | POST /devices로 새 등록 후 새 token으로 재전송 |
| 404 | 모르는 content_id (단건) | 그 이벤트 버림 |
| 413 | 배치 500개 초과 | 500개 이하로 나눠 재전송 |
| 422 | 단건: 필수 필드 누락, 모르는 type | 그 이벤트 버림 |
| 422 | 배치: 봉투가 JSON이 아니거나 events 배열 없음 | 앱 버그. 큐 보존, 보고 |
| 422 | interests: catalog에 없는 category | 저장되지 않음. 앱이 보내는 값을 고친다 |
| 5xx, 무응답 | 서버 장애, 네트워크 | 큐 보존. 같은 배치를 나중에 다시 |

## DB

SQLite 단일 파일로 처리하며, 테이블은 다섯개를 쓴다.

| 테이블 | 열 |
|---|---|
| device | id, install_id, user_id UNIQUE, token UNIQUE, app_version, platform, created_at, last_seen_at |
| event | id, event_id UNIQUE, user_id, content_id, content_version, playback_id, seq, type, position_sec, playback_rate, client_ts, received_at |
| listen_state | (user_id, content_id) UNIQUE, last_position_sec, played_sec, completed, finished, skipped, liked, saved, last_ts, content_version, duration_at_version, base_version, prev_completed, updated_at |
| ad_event | id, event_id UNIQUE, user_id, ad_id, playback_id, seq, type, position_sec, client_ts, received_at |
| user_interest | user_id UNIQUE, interests JSON, updated_at |

- 인덱스: event(user_id, content_id, seq), ad_event(user_id, seq)
- event와 ad_event는 지우거나 고치지 않는다. listen_state는 이벤트 저장과 같은 트랜잭션에서, 그 사용자·콘텐츠의 이벤트 전체를 다시 접어 덮어쓴다. 범위가 한 사용자·한 콘텐츠라 비용은 작다.
- 광고 이벤트는 청취 기록과 다른 테이블에 둔다. 청취 기록은 광고 선택에 쓰지 않는다.

## 코드 변경

server/teum/listening/ 기준. 다른 모듈은 표의 마지막 세 줄만 바뀐다.

| 파일 | 지금 | 바뀜 |
|---|---|---|
| listening/schemas.py | 8종. user_id, content_id, type, position_sec | 15종. event_id, playback_id, seq, client_ts, playback_rate, content_version 추가. user_id 제거. 배치, device 등록, interests 모델 |
| listening/router.py | POST /events, GET /history?user_id | 토큰 의존성. POST /devices, POST /events/batch, PUT·GET /me/interests 추가. GET /history는 토큰으로 |
| listening/store.py | event 테이블 하나. 인덱스 없음. 조회마다 전체 이벤트를 다시 접음 | 테이블 다섯. 인덱스. 저장과 같은 트랜잭션에서 그 사용자·콘텐츠의 이벤트 전체를 seq 순으로 다시 접어 listen_state 덮어씀(배치는 사용자·콘텐츠별로 한 번). interests(user_id) |
| listening/state.py | 마지막 위치 ≥ 80% 또는 complete면 completed. like가 위치·last_ts를 바꿈. last_ts는 서버 시각 | 이벤트 집합 → listen_state의 순수 함수. completed는 played_sec으로만, finished는 현재 재생 기준. 중단 사유는 정지·안내 둘. like·save는 위치·last_ts를 바꾸지 않음. last_ts는 client_ts를 epoch 초로. playback_rate, content_version 처리 |
| tests/test_listening.py | 4건, xfail 1건 | like에 position이 실린 경우, 중복 배치, 같은 이벤트 집합을 섞어 넣어도 listen_state가 같은지, 안내 중 정지, 완청 뒤 재청취, 무토큰, 배속, content_version 추가. xfail은 통과하도록 |
| tests/test_playlist.py | complete 한 건으로 playlist에서 빠지는지 확인 | 새 요청 형식과 새 completed 규칙에 맞게 |
| core/deps.py | Catalog, Search, Listening 의존성 | 토큰 → user_id 의존성 추가. 기존은 그대로 |
| player/static/player.html | user_id, content_id, type, position_sec만 전송 | device 등록 후 token, event_id, playback_id, seq, client_ts, playback_rate, content_version을 붙여 전송 |
| catalog | version 없음 | version 정수 컬럼. GET /contents 응답에 포함 |
| playlist/router.py | interests 쿼리만 | 쿼리가 비면 listening.interests(user_id) |

구현 순서: store → state → POST /events → POST /devices와 토큰 → POST /events/batch → interests. 단계마다 테스트를 붙인다.
