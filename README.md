# 온에어 (OnAir) · 팀 히든트랙

운전하면서 화면 없이 듣는 오디오 콘텐츠 서비스입니다. 차량 앱(Pleos Connect)은 듣는 사람의 관심사와 도착까지 남은 시간에 맞춰 이야기를 골라 이어서 틀어 줍니다. 그 이야기는 AI 제작 파이프라인이 매일 새로 만듭니다.

- 앱 API: `https://api.onaircar.com/api/v1` (차량 앱이 접속하는 주소, APK와 함께 바로 이용 가능)
- 제작 사이트: `https://studio.onaircar.com/admin/` (제작진 내부용, 팀 계정 로그인 필요)
- 청취 웹앱: `https://studio.onaircar.com/app/` (웹 브라우저에서 콘텐츠를 들어 보는 제작진 내부용 화면, 팀 계정 로그인 필요)

## 데모 영상

https://github.com/user-attachments/assets/7d09b469-885a-4d5a-b092-d901e39aafe8

영상 파일: [`demo/온에어_소개데모.mp4`](demo/온에어_소개데모.mp4) (1분 30초)

## 차량 앱 (Pleos Connect)

Pleos Connect(Android Automotive)용 오디오 앱입니다. 짧은 이야기를 이동 시간에 맞춰 편성해 들려줍니다.

- **추천 듣기:** 주제를 고르지 않아도 바로 목록을 짜서 재생합니다.
- **골라 듣기:** 다섯 분야 중 여러 개를 고르고, 들을 시간(15·30·60분)을 정합니다. 내비게이션으로 경로 안내 중이면 "도착까지 N분" 선택지가 나타나고, 도착 시간에 맞춘 목록을 만듭니다.
  - A 사람과 일상(직장·연애·가족·인간관계)
  - B 생활과 취향(살림·음식·소비·여행·건강)
  - C 지식과 세상(과학·AI·역사·인문·사회)
  - D 문화·취미·이동(영화·음악·스포츠·교통)
  - E 성경과 신앙(성경 낭독·묵상)
- **깊이 듣기:** 두 진행자가 대화하는 팟캐스트(한 편 8~9분)를 홈에서 골라 듣습니다. 재생 화면에 두 진행자가 표시됩니다. 목록과 음원은 `GET /api/v1/longform`에서 받습니다.
- **재생 화면:** 재생·정지, 이전·다음, 배속(0.8·1.0·1.2·1.5), 다음 목록 보기를 지원합니다. 좋아요와 나중에 듣기를 누르면 다음 추천에 반영됩니다.
- **브리핑:** 홈 퀵 메뉴에서 오늘의 날씨·경제 브리핑을 보고, 차량 음성 합성으로 들을 수 있습니다.
- **루틴:** 매일 듣는 순서를 묶어 두고 버튼 하나로 시작합니다. 순서 편집은 정차 중에만 됩니다.
- **차량 상황 대응:** 뒷좌석에 사람이 타면 주행마다 한 번 가족 콘텐츠로 바꿀지 묻습니다. 목적지에 도착하면 듣던 편까지만 재생합니다.

### 바로 실행

- APK 위치: `apk/히든트랙_온에어_release.apk`
- 테스트 환경: AVD `Pleos_Connect_v2.1.0` (`system-images;android-34;pleos_car_emulator;arm64-v8a`, 2560×1440 mdpi, RAM 4GB, Cold boot)
  - 앱은 화면 오른쪽 앱 영역에서 실행됩니다.
  - 경로 안내: 에뮬레이터 내비게이션에서 목적지를 검색해 안내를 시작합니다.
  - 차량 상태 바꾸기: `adb forward tcp:18088 tcp:8088`을 실행한 뒤 PC 브라우저에서 `http://localhost:18088`을 엽니다. 이 Virtual Console에서 좌석 탑승, 기어, 속도를 바꿀 수 있습니다.
- 설치:
  ```bash
  adb install -r apk/히든트랙_온에어_release.apk
  ```
- 서버 연결: 앱은 `https://api.onaircar.com/api/v1`에서 목록과 음원을 받습니다. 서버 주소는 빌드할 때 들어가므로 따로 설정하지 않아도 됩니다. 인터넷 연결이 필요하며, 첫 실행 때 기기 등록이 자동으로 이루어집니다.
- 둘러보는 순서:
  1. 앱을 실행하고 홈에서 추천 듣기를 누릅니다.
  2. 골라 듣기에서 주제와 시간을 고릅니다.
  3. 에뮬레이터 내비게이션으로 경로 안내를 시작하고, 골라 듣기에 나타나는 "도착까지 N분"을 확인합니다.
  4. 재생 화면에서 좋아요와 나중에 듣기를 누릅니다.
  5. 홈의 깊이 듣기에서 팟캐스트 한 편을 재생합니다.
  6. 홈 퀵 메뉴에서 브리핑과 루틴을 실행합니다.
  7. Virtual Console(`http://localhost:18088`)에서 뒷좌석 탑승을 켜고, 홈에 나타나는 "가족 콘텐츠로 바꿀까요?" 제안을 확인합니다.

### Pleos SDK와 차량 환경 대응

Pleos SDK는 공개 저장소 `https://nexus-playground.pleos.ai/repository/maven-releases/`에서 받습니다(`settings.gradle.kts`에 지정).

| SDK | 용도 | 권한 |
| --- | --- | --- |
| NaviHelper 3.0.5 | 목적지까지 남은 시간·거리, 도착·경로 종료 알림 | `pleos.car.permission.NAVI_ROUTE` |
| Vehicle 2.3.2 | 좌석 탑승 | `pleos.car.permission.CAR_SEATS` |
| FusedLocation 1.1.3 | 날씨 위치 | `pleos.car.permission.FUSED_LOCATION` |
| TextToSpeech 3.3.1 | 브리핑 낭독 | `pleos.car.permission.TTS_SERVICE` |

- SDK나 서비스가 없거나 값을 받지 못하면, 시간을 직접 고르는 기본 동작으로 돌아갑니다.
- 위치와 좌석 값은 목록을 요청할 때 서버로 보내기만 하고, 앱에 저장하지 않습니다.
- Pleos 앱 심사 규칙 대응:
  - Media3 ExoPlayer와 MediaSessionService로 재생하고, `android:appCategory="audio"`를 지정했습니다.
  - 앱이나 서비스가 시작되는 것만으로는 재생하지 않습니다. 사용자가 눌렀을 때만 재생합니다.
  - 오디오 포커스를 처리하고, 이어폰이 빠지면 멈춥니다. 최근 앱 목록에서 지우면 재생을 멈추고 서비스를 끝냅니다.
  - 주행 중에는 루틴 순서 편집을 잠급니다(CarUxRestrictions).

### 소스에서 빌드

- 필요한 도구: JDK 17 이상, Android SDK Platform 36.1(compile), targetSdk 34, minSdk 28. Gradle 9.3.1, AGP 9.1.1, Media3 1.8.0.
- 빌드와 단위 테스트:
  ```bash
  cd apps/car-app
  ./gradlew :app:assembleRelease -Pteum.serverOrigin=https://api.onaircar.com
  ./gradlew :app:testDebugUnitTest
  ```
- 서명: 서명 키는 저장소에 없습니다. Android Studio의 Build > Generate Signed App Bundle / APK에서 직접 만든 키로 서명하거나, 명령줄에서 `assembleRelease`에 아래 옵션을 붙입니다.
  ```bash
  -Pandroid.injected.signing.store.file=<키 파일> -Pandroid.injected.signing.store.password=<비밀번호> \
  -Pandroid.injected.signing.key.alias=<별칭> -Pandroid.injected.signing.key.password=<비밀번호>
  ```

### 알아 둘 점

- 80% 이상 들은 이야기는 다음 편성에서 빠집니다. 준비된 이야기를 다 들으면 빈 목록 화면이 나오는데, 거기서 "새 사용자로 다시 받기"를 누르면 처음부터 다시 들을 수 있습니다.
- 맥에서 Pleos 에뮬레이터를 실행하면 음원이 실제보다 느리게(약 0.7배) 재생되는 현상이 있습니다.

## AI 콘텐츠 제작 파이프라인

| 단계 | 하는 일 | 모델·도구 | 코드 |
|---|---|---|---|
| ① 수집 | 매일 05:00 뉴스와 이슈를 모아 소재로 정리합니다 | Gemini (검색 그라운딩, 배치) | `ai/issue_collector`, `ai/source_collector` |
| ② 대본 생성 | 소재로 90초 안팎의 차량용 대본을 쓰고 스스로 검토·수정합니다 | 작성 Gemini, 검토 GPT | `ai/script_agent` |
| ③ 검수 | 사실, 안전, 듣기 적합성을 검사하고, 문제가 있으면 수정 지시 후 다시 검수합니다 | Claude, 안전 분류기, 중복 검사(임베딩) | `ai/refine` |
| ④ 음성 | 편마다 목소리를 골라 합성하고, 받아쓰기로 대본과 대조합니다 | Typecast, Whisper | `services/studio`, `ai/tts` |
| ⑤ 게시 | 담당자가 음원을 들어 보고 게시하면 앱 목록에 올라갑니다 | | `ai/content`, `server/teum` |
| 토크쇼 | 앱의 깊이 듣기에서 재생하는 두 사람 대화형 8~9분 회차를 만듭니다. 자료 검색 → 근거 카드(원문 인용 대조) → 대본 → 검토 → 음성 순서입니다 | Gemini, GPT, Typecast | `ai/longform` |
| 피드백 | 제작진이 단계별로 남긴 의견을 매일 04:30 프롬프트 옵티마이저가 규칙으로 정리해 다음 제작에 반영합니다 | Gemini | `ai/factory` (`feedback.py`) |

자동 생산 루프(`ai/factory`)가 ①~④를 이어서 실행합니다. 게시 여부는 사람이 음원을 듣고 정합니다.

## 폴더 구성

```
apps/car-app        차량 앱 (Android, Pleos Connect)
apk                 release APK (히든트랙_온에어_release.apk)
demo                시연 영상
server/teum         앱 API: 카탈로그, 추천·편성, 청취 기록, 루틴, 브리핑, 검색
services/studio     제작 사이트(/admin), 청취 웹앱(/app), 공개 입구(teum_gate: /api/v1 공개, /admin 로그인)
ai/factory          자동 생산 루프, 피드백 → 프롬프트 옵티마이저
ai/issue_collector  ① 이슈 수집        ai/source_collector  생활 사연 원천 수집·AI 창작 사연
ai/script_agent     ② 대본 생성        ai/refine            ③ 검수
ai/content          게시 파이프라인     ai/tts, ai/longform, ai/recommendation
infra/one-server    서버 한 대에서 전체 실행 (teum_up.py)
content/demo_data   데모용 카탈로그와 음원 (공개 라이선스를 확인한 52편 + 광고 4편)
docs                API 연동 안내, 화면 기능 명세
```

## 서버 실행

서버 한 대에서 API, 제작 사이트, 자동 생산 루프를 함께 띄웁니다. 자세한 내용은 [`infra/one-server/README.md`](infra/one-server/README.md)에 있습니다.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r infra/one-server/requirements.txt && .venv/bin/pip install -e ai/script_agent
export TEUM_HOME=$PWD/var/home
.venv/bin/python infra/one-server/teum_up.py init   # 폴더, teum.env, 계정 생성 (API 키는 teum.env에 직접 입력)
.venv/bin/python infra/one-server/teum_up.py up     # API :8790, 제작 사이트 :8780, 자동 생산 루프
```

데모 데이터로 API만 띄우기(개발·확인용. 제공된 APK는 `https://api.onaircar.com`에 고정되어 있어 로컬 서버에 연결하려면 `-Pteum.serverOrigin`으로 다시 빌드해야 합니다):

```bash
cd server
TEUM_DATA_ROOT=../content/demo_data TEUM_EVENTS_DB=/tmp/teum-events.sqlite TEUM_DENSE=0 ../.venv/bin/python -m uvicorn teum.main:app --port 8765
```

API 키(Gemini, OpenAI, Anthropic, Typecast)는 저장소에 없습니다. `TEUM_HOME/teum.env`와 키 파일에 넣어 사용합니다.

## 테스트

```bash
(cd server && ../.venv/bin/python -m pytest -q)
(cd ai/factory && PYTHONPATH=src ../../.venv/bin/python -m pytest -q)
(cd ai/content && PYTHONPATH=src ../../.venv/bin/python -m pytest -q)
(cd ai/refine && PYTHONPATH=src ../../.venv/bin/python -m pytest -q)
```
