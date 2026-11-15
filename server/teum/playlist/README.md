# 추천 본체 · 함수 호출 계약

2026-11-05 최신: 추천 함수는 데이터 입력 기반으로 유지하고 playlist/router.py에서 기존 GET /api/v1/playlist에 연결했다. 기존 user_id·minutes·interests·speed 요청을 그대로 사용하면 새 추천을 실행한다. 세션 API·청취 DB 변경·플레이어 변경·GET /topics는 추가하지 않았다.

S1-11·S1-12 최신 통합 계약은 [추천·편성 구현 문서](../../../docs/sprints/S1-11_S1-12_추천_편성_구현.md)를 따른다. 광고 하한 10%/상한 15%, 선택 대분류별 이야기 시간 하한 10%, 광고 중복 회피와 이야기 종료를 구현했다. `TEUM_PLAYLIST_POLICY_FILE`로 비중을 변경하며 관리자 저장소용 공급자 교체 지점을 제공한다. 유한 골라 듣기에는 5개 중 최대 3개 대신 시간 비중 정책을 적용한다.

## 실제 HTTP 연결

```http
GET /api/v1/playlist?user_id=u_example&minutes=30
GET /api/v1/playlist?user_id=u_example&minutes=30&mode=pick&interests=C06,C07&speed=1.5
```

- mode 생략/`recommend`: 새 추천 본체. `pick`: 전달된 주제만 허용. `legacy`: 이전 정렬을 명시적으로 사용.
- interests 생략·빈 문자열·공백/쉼표만 있으면 기존 ListeningStore.interests(user_id)를 읽는다. 값이 있으면 쿼리가 우선하며 저장값을 바꾸지 않는다. 같은 사용자 history의 좋아요·보관·완청·실청취 상태를 사용한다.
- user_id 쿼리 계약과 기존 items/total_sec/budget_sec/leftover_sec/rule 필드를 유지한다. 새 추천은 근거·algorithm_version·shortage·packing·signal_status를 추가한다. 응답 모양은 호환되지만 기본 정렬은 의도적으로 새 추천으로 바뀐다.
- PR #10 정본의 C01–C21은 공급 여부와 관계없이 쿼리에서 유효하다. 기존 영어 category는 현재 이야기 카탈로그에 존재할 때 호환 허용한다. A–E는 앱에서 C코드로 펼쳐 보내며 라우터가 영어 카테고리를 임의 이관하지 않는다.
- 저장 관심사 API의 유효성 검사 자체는 변경하지 않았다. 따라서 공급 없는 코드 저장 허용 여부는 listening 담당의 기존 계약을 따른다.
- 공개 카탈로그 중 실제 파일이 있는 음원만 새 추천에 넣는다. 인증·사용자 등록 방식은 기존 계약을 유지한다.
- 실제 저장/조회 연결이 없는 선택 이력과 스킵 타이밍은 빈 입력으로 전달한다. 응답 signal_status에 selection_history/skip_timing=`not_connected`를 표시한다. 실제 청취 이력의 skipped 값만으로 타이밍 감점을 추정하지 않는다.
- next_candidates는 독립 함수이며 세션/연속 보충 HTTP API를 이번에 만들지 않았다. GET /playlist는 여전히 유한 시간 편성이다.

## PR #10 분류 정본

`server/teum/catalog/categories.py`는 PR #10 head `3978b63a6b98af3621d6b66453badb728137c8e2`의 파일을 수정 없이 가져왔다. 같은 PR의 플레이어·검색 변경은 가져오지 않았다. playlist/topics.py는 정본 CODES/GROUPS/GROUP_NAMES를 읽는 호환 뷰이며 코드 범위나 이름을 중복 정의하지 않는다. 원본에 포함된 migrate CLI는 자동 실행하지 않으며 DB 이관도 하지 않았다. 모듈 import는 분류 정의를 읽을 뿐 DB 접속을 수행하지 않는다.

최신 알고리즘 `r1-group-cap-direct-skip-v5`: 대체 후보가 있으면 연속5개 슬롯에서 같은 A–E 대분류를 최대3개로 제한한다. 단일 대분류 골라 듣기 또는 주제/시간/공급 제약으로 대체 후보가 없으면 제한을 완화하며 선택 밖 주제를 끼워 넣지 않는다. 스킵 감점은 해당 콘텐츠에만 적용하고 같은 세부 주제에 전파하지 않는다. B0 기준선과 기존 HTTP API는 유지한다.

## 제공 함수

| 함수 | 역할 |
| --- | --- |
| ranker.rank_candidates | 입력 후보에 점수·근거를 붙여 정렬. (ranked, available) 반환 |
| recommendation.recommend | 추천 순위와 정책 기반 시간·광고·주제 하한 편성 결합. items·부족 사유·시간 잔여 반환 |
| queue_candidates.next_candidates | 현재·대기·소비 ID를 제외하고 대기 목록에 추가할 이야기 후보만 반환 |
| selection_preferences.group_preferences | 전달받은 골라 듣기 이력으로 대주제 선호 계산 |
| skip_preferences.skip_penalties | 전달받은 사용자 다음 이벤트로 보수적인 감점 계산 |

추천 계산은 표준 라이브러리·playlist 내부 모듈·공통 분류 정의를 사용한다. HTTP·FastAPI·DB 조회·앱 상태·listening 구현에 의존하지 않는다. router는 별도 서버 연결 코드이며 함수 본체 import에 필요하지 않다.

## 입력 계약

- contents: 공개·채널·음원 가용성을 호출자가 확인한 콘텐츠 dict 목록. 필수 content_id, kind, title, category, duration_sec(원본 초), audio_uri. 선택 tags, play_no, license, attribution. 완청 콘텐츠 메타데이터도 선호 계산에 활용한다.
- history: content_id별 dict. liked, saved, completed, played_sec를 참조한다. 공식 완청 판정은 청취 담당 결과를 받아 사용한다.
- interests: 이번 선택 또는 저장 관심사를 호출자가 조회해 확정한 C코드 목록. 자동 fallback이나 저장은 없다. A–E 확장은 playlist.topics.GROUPS 참고.
- groups: 다양성에 사용할 A–E 목록. 실제 주제 필터는 interests이며 groups가 이를 확대하지 않는다.
- selections: TopicSelection(selection_id, selected_at, group_ids, playback_started) 목록. selected_at은 timezone 포함 datetime. 첫 실제 재생된 선택만 반영하며 최근30일·14일 반감기·UTC 하루 총기여1 상한·중복 제거를 적용한다.
- events: 기본 빈 목록. 사용자별 정규화·중복 제거된 재생 이벤트 dict 목록. event_id, content_id, playback_id, seq, type, position_sec, playback_rate, client_ts(Unix 초), content_version을 사용한다. 마지막 skip에 skip_reason=user_next가 있어야 감점한다. 이것은 본체 입력 계약이며 기존 HTTP 이벤트 스키마에 추가하지 않았다. 저장 형식은 담당자가 결정하고 변환한다.
- now: 재현 가능한 평가에는 timezone 포함 datetime 전달. 생략하면 현재 UTC 시각 사용.

입력은 유효한 데이터여야 한다. 사용자 인증·카탈로그 공개 승인·DB 조회는 호출자가 담당한다. events/selections가 없으면 해당 신호는 중립이다.

## 다음 5개 보충 예

```python
from datetime import datetime, timezone
from teum.playlist.queue_candidates import next_candidates

additions = next_candidates(
    contents, history,
    current_id=current_content_id,
    queued_ids=[item["content_id"] for item in pending_queue],
    consumed_ids=consumed_story_ids,
    interests=effective_interests,
    groups=selected_groups,
    mode="recommend",  # 골라 듣기는 "pick"
    selections=selection_records,
    events=normalized_events,
    now=datetime.now(timezone.utc),
    target_size=5,
    remaining_sec=None,  # 골라 듣기는 현재/대기 예약분을 뺀 가용 초
    speed=1.0,
)
# 호출자가 기존 pending_queue 뒤에 additions를 예약한다.
```

대기3개이면 최대2개를 반환한다. 현재·대기·소비 콘텐츠는 제외하며 기존 목록을 수정하지 않는다. 공급/주제/시간이 부족하면 적게 반환하고 중복으로 채우지 않는다. 광고를 삽입하지 않으며 대기 광고도 queued_ids의 한 슬롯으로 센다. 광고 예약·재생 전환·재시도·동시성·배속·예산·세션 영속 저장은 호출자 책임이다. 호출 자체가 청취·스킵·선택 이력을 만들지 않는다.

분산 계산에는 기존 queued_ids와 새 후보를 함께 사용하고 current_id는 포함하지 않는다. 예약된 이야기의 분류를 확인할 수 있도록 contents에 해당 메타데이터를 함께 전달한다. 이미 편중된 기존 목록은 재정렬하지 않는다. 카탈로그에 없는 예약 ID는 분류를 추측하지 않고 슬롯만 차지한다. 광고도 슬롯만 차지하며 A–E 개수에는 포함하지 않는다. 시간 편성은 실제 남은 시간에 들어갈 후보 중에서 같은 규칙을 적용한다.

## 시간 편성 예

```python
from teum.playlist.recommendation import recommend
result = recommend(contents, minutes=30, history=history,
                   interests=["C06", "C07"], groups=["B"],
                   mode="pick", speed=1.0,
                   selections=selection_records, events=normalized_events)
```

recommend는 planner의 광고 비중·주제 하한 정책을 사용한다. 선택적 policy 인자로 PlaylistPolicy를 전달할 수 있다. 기존 scheduler의 이야기2편마다 광고는 mode=legacy에만 남아 있다. 순위/큐 후보만 필요하면 rank_candidates/next_candidates를 호출한다. next_candidates는 전체 편성의 광고/주제 하한을 관리하지 않는다.

## 추천 규칙

명시 관심사4, 카테고리 반응과 태그 유사도, 미청취0.5, 보관0.3, 과거 대주제 선택 선호0.3을 조합한다. 좋아요·보관·완청은 선호 증거로 사용하며 완청 콘텐츠는 자동 후보에서 제외한다. C21은 명시 선택일 때만 허용한다. pick은 전달된 C코드만 허용한다.

스킵은 0부터 시작한 최신 재생에서 실제 사용자 다음임이 확인될 때만 반영한다. 실제 재생10초 이하이면서 청취비율20% 미만이면0.5, 나머지80% 미만이면0.2 감점하고14일 반감기를 적용한다. 탐색·중단·불완전 로그와 좋아요/보관/완청 콘텐츠는 감점하지 않는다. listening.fold_content 호출을 제거했으며 검증된 연속·단조 증가 구간만 합산한다. 공식 완청 판정이 아닌 추천 특성 계산이다.

## 담당 경계와 검증

추천 규칙 담당: 추천 함수·입력 계약·playlist HTTP 연결·테스트. 청취 담당: history/선택/이벤트 저장·공개 조회 계약. 앱 담당: 요청·큐/세션 관리·재생·전송·복원. 현재 HTTP 연결은 관심사와 기존 반응/실청취에 적용되며 추가 선택/스킵 입력 연결은 담당자 협의 후 진행한다.

기존 listening/player/main은 수정하지 않았다. 과거 세션 통합 코드는 저장소 밖 프로젝트 result/recommendation_decoupling_20261105/에 보관했다. 콘텐츠 대본 작업은 변경하지 않았다. API 테스트는 저장/취소에 따른 실제 순위 변화·관심사 fallback/쿼리 우선/사용자 격리·C코드·종교 필터·음원 부재·legacy·입력 오류를 검증한다.

server에서 `../.venv/Scripts/python.exe -m pytest -q tests`로 검증한다. 세션/플레이어 확장 테스트는 철회한 기능과 함께 보관했고 추천 단위 테스트와 기존 서버 회귀 테스트는 유지한다. 브라우저·차량·배포 검증은 아니다.
