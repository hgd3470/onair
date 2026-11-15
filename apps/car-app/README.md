# 온에어 차량 앱 (MVP · 11/5 시연용)

Pleos Connect(Android Automotive)용 오디오 앱. 화면 명세는 [ui-functional-spec](../../docs/sprints/ui-functional-spec.md), 서버 계약은 [api-integration-guide](../../docs/sprints/api-integration-guide.md)·[listening README](../../server/teum/listening/README.md)를 따른다.

## 범위

| 화면 | 구현 |
| --- | --- |
| S01 홈 | 추천 듣기(관심사 없음·60분 편성) / 골라 듣기. 재생 중이면 상단에 '듣는 중' 바로가기 |
| S02 골라 듣기 | A–E 복수 선택, 15/30/60분(기본 30), 0개면 CTA 비활성 |
| 목록 준비·오류 | 로딩·취소, 네트워크 오류 재시도, 빈 편성(다 들은 경우) 안내 |
| S04 재생 | 재생/정지, 이전/다음, 배속 0.8/1.0/1.2/1.5(앱 재실행 후 유지), 좋아요, 나중에 듣기, 다음 목록 최대 5개 |
| S05 광고 | kind=ad 이면 '광고' 표시, 이전/다음·좋아요·보관 없음 |

| 홈 퀵 메뉴 (S2-12) | 오늘 날씨·경제 브리핑·고속도로 교통상황 카드. 아이콘·제목·부제(서버 값)·"약 N분"·재생 버튼·출처. available=false 면 흐리게 + 짧은 안내 |
| 홈 내 루틴 (S2-12) | 이름·label·목적지까지 N분·단계 칩·순서 편집·루틴 시작 |
| 루틴 화면 (S2-12) | 듣는 순서(끌어서 변경·항목 추가·빼기, 정차 중에만) / 지금 듣는 중·진행·이전/재생/다음 / 다음 순서 / "N / M 재생 중" |
| 차량 맥락 (S2-11) | 내비 남은 시간·거리, 좌석 탑승, 뒷좌석 탑승 시 가족 콘텐츠 확인(주행당 1회), 도착 시 듣던 편까지만 재생 |

제외: S06 저장 목록, S08 설정, 하단 목록 끌어올리기, 오프라인 이벤트 큐.

대분류 → interests: A=C01–C05, B=C06–C12, C=C13–C17, D=C18–C20, E=C21 (`data/Models.kt`의 `Topics`).

## 서버 연동

- 최초 실행: `POST /devices` → user_id·token 을 앱 전용 SharedPreferences 에 저장. 이후 재등록하지 않음. 401(모르는 토큰)이면 한 번 재등록.
- 편성: `GET /recsys/playlist?user_id=&minutes=|remain_sec=&interests=&speed=&mode=&hour=&passengers=&family=`. 경로 안내 중이면 minutes 대신 remain_sec(추천 듣기, 골라 듣기의 '도착까지 N분'). 값이 없으면 보내지 않는다. `audio_uri` 는 호스트(SERVER_ORIGIN)에 붙여 스트리밍.
- 골라 듣기 시작: `POST /selections {selection_id, interests:[C코드], selected_at}` 후 그 목록의 play 이벤트에 같은 selection_id.
- 퀵 메뉴: `POST /quick-menu {lat, lon, remain_sec, remain_m}` (위치는 소수 둘째 자리, 본문으로만, 저장 안 함).
- 루틴: `GET/PUT /me/routines`, `POST /routines/{id}/plan` → items 를 start_at_sec 순서로 한 큐에. skipped 단계는 회색 "정보 없음".
- 이벤트: `POST /events` (Bearer). event_id(UUID), playback_id(재생마다 새 UUID), seq(기기 전체 증가·저장 유지), content_version=1, playback_rate, client_ts(ISO 8601 + 오프셋).

| 이벤트 | 시점 |
| --- | --- |
| play | 콘텐츠 첫 재생(새 playback_id) |
| position | 재생 중 10초마다, 정지·다음·이전·배속 변경 직전(배속 변경은 직후에도) |
| pause / resume | 사용자 정지·포커스 상실(통화 등) / 재개 |
| skip | skip_reason: 다음 버튼·스티어링 다음 키 `user_next`, 이전 `user_prev`, 도착으로 멈춤 `arrival`, 재생 오류 `error` |
| complete | 끝까지 재생 |
| like/unlike, save/unsave | 토글. 실패하면 화면 상태를 되돌림 |
| ad_start / ad_complete / ad_error | 광고 시작·종료·오류 (rate·version 없음) |

- 좋아요·보관 상태는 편성 직후 `GET /history`의 liked/saved 로 반영.
- 오프라인 큐는 없다. 실패 시 메모리에서 최대 3번 재시도한 뒤 버린다.
- 브리핑은 콘텐츠가 아니라 이벤트를 보내지 않는다(서버가 unknown_content 로 거절).

## 브리핑 재생

- 같은 ExoPlayer 큐에 넣어 MediaSession·스티어링 키·오디오 포커스가 이야기와 똑같이 동작한다.
- Pleos 차량: Pleos TextToSpeech(ON_DEVICE, `Locale.KOREA` — "ko"만 주면 엔진이 거절)는 직접 소리를 내는 speak() 만 있어, 큐에는 무음 자리(WAV)를 넣고 그 항목이 재생되는 동안 읽는다(`playback/LiveBriefing.kt`). 다 읽으면 다음으로, 일시정지·포커스 상실이면 멈추고 다시 재생하면 처음부터 읽는다.
- 그 밖의 기기: Android TextToSpeech(Locale.KOREAN) `synthesizeToFile` 결과를 재생한다. Pleos 음성 서비스는 Android TTS 엔진 목록에 나오지 않는다.
- 화면에는 subtitle 과 sources[].name 을, 소리로는 text 를 쓴다.

## Pleos SDK

`settings.gradle.kts` 에 공개 저장소 `https://nexus-playground.pleos.ai/repository/maven-releases/`.

| SDK | 쓰는 곳 | 권한 |
| --- | --- | --- |
| NaviHelper 3.0.5 | getDestinationInfo → remainTime(초)·remainDistance(m), onDestinationArrived, onRouteEnded/Cancelled | `pleos.car.permission.NAVI_ROUTE` |
| Vehicle 2.3.2 | Seat getCurrentSeatOccupancy·registerSeatOccupancy | `pleos.car.permission.CAR_SEATS` |
| FusedLocation 1.1.3 | 날씨 위치(없으면 Android LocationManager) | `pleos.car.permission.FUSED_LOCATION`, `ACCESS_COARSE_LOCATION` |
| TextToSpeech 3.3.1 | 브리핑 낭독 | `pleos.car.permission.TTS_SERVICE` |

- SDK·서비스가 없거나 값을 못 받으면 조용히 기존 동작(수동 시간, passengers·위치 미전송)으로 돌아간다.
- 좌석: Vehicle SDK 2.3.2 는 원시값 1 을 true 로 주는데, 에뮬레이터·Virtual Console 은 Android 표준(1=비어 있음, 2=탑승)으로 낸다. 앱은 SDK false 를 탑승으로 본다(`SeatSource.SDK_TRUE_MEANS_OCCUPIED`). 실차에서 확인 필요.
- 주행 제한: `CarUxRestrictions`(android.car, required=false). 제한 중이거나 상태를 못 받으면 루틴 순서 편집을 잠그고, 편집 중 주행으로 바뀌면 편집을 닫는다.
- 위치·좌석 값은 요청 때 서버로 보내기만 하고 앱에 저장하지 않는다.

## Pleos 심사 규칙 대응

- Media3 ExoPlayer + `MediaSessionService` (`playback/PlaybackService.kt`)
- `android:appCategory="audio"`
- 앱·서비스 시작만으로 재생하지 않음. 재생은 사용자가 '추천 듣기'/'듣기'를 눌렀을 때만. 재생 재개(resumption) 미지원
- `setAudioAttributes(..., handleAudioFocus = true)`, 이어폰 분리(noisy) 시 정지
- 최근 앱에서 지우면 정지 후 서비스 종료

## 빌드·실행

JDK 17 이상, Android SDK Platform 36.1(compile), targetSdk 34, minSdk 28. Gradle 9.3.1 / AGP 9.1.1 / Media3 1.8.0.

```bash
cd apps/car-app
./gradlew :app:assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:testDebugUnitTest
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

서버 주소 기본값은 `http://10.0.2.2:8780` (에뮬레이터 → 개발 PC localhost → SSH 터널). 바꾸려면 `-Pteum.serverOrigin=http://host:port`. HTTP 는 `res/xml/network_security_config.xml`에서 10.0.2.2·127.0.0.1·localhost 만 허용하며, 다른 HTTP 호스트를 쓰면 그 파일에도 추가해야 한다.

### 서버 터널

```bash
ssh -N -L 8780:127.0.0.1:8780 <서버 호스트>
curl http://127.0.0.1:8780/api/v1/contents    # 맥에서 확인
```

### 완료 확인 (좋아요 → history)

```bash
# Pleos(AAOS)는 앱이 차량 사용자(user 10)로 실행된다. 폰 에뮬레이터는 --user 10 을 빼고 실행.
TOKEN=$(adb shell run-as com.hiddentrack.onair --user 10 cat shared_prefs/teum_device.xml | sed -n 's/.*name="token">\([^<]*\)<.*/\1/p')
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8780/api/v1/history
```

`adb logcat -s TeumEvents TeumAudio TeumNavi TeumSeat TeumPleosTts` 로 보낸 이벤트(type·seq·위치)와 음원 요청 주소·HTTP 응답 코드를 볼 수 있다.

### Pleos Connect 에뮬레이터

AVD `Pleos_Connect_v2.1.0` (system-images;android-34;pleos_car_emulator;arm64-v8a, 2560×1440 mdpi, RAM 4GB, Cold boot). 앱은 오른쪽 앱 영역에 뜬다.
Virtual Console(에뮬레이터 안 `http://<에뮬레이터>:8088`, `adb forward tcp:18088 tcp:8088`)에서 좌석 탑승·기어·속도를 바꿀 수 있다. 경로는 에뮬레이터 내비에서 검색해 안내를 시작한다.
이 에뮬레이터는 맥에서 음원이 실제 시간보다 느리게(약 0.7배) 진행되는 현상이 있어 서버 played_sec 이 0 에 가깝게 집계될 수 있다(폰 에뮬레이터에서는 정상).

## 시연 시 주의

- 서버는 completed(80% 이상 청취)인 이야기를 편성에서 뺀다. 리허설로 다 들으면 빈 목록이 나오며, 그 화면의 '새 사용자로 다시 받기'로 새 user_id 를 받아 다시 시작할 수 있다.
- 현재 카탈로그에 광고가 없어 광고 화면은 실제 서버 데이터로는 확인하지 못했다.
