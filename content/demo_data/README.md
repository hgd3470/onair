# 앱 데모용 콘텐츠 데이터

`TEUM_DATA_ROOT`로 바로 지정할 수 있는 서버 데이터 폴더입니다. 제작 파이프라인(`ai/content`)이 게시한 결과의 사본입니다. 서버는 공개(`published`) 콘텐츠만 앱에 내보내고, 검수 대기(`review_pending`)는 목록·음원 제공에서 빠집니다.

```bash
cd server
TEUM_DATA_ROOT=../content/demo_data TEUM_EVENTS_DB=/tmp/teum-events.sqlite TEUM_DENSE=0 \
  uvicorn teum.main:app --port 8765
```

- `outputs/catalog.sqlite`: 공개 이야기 52편과 광고 4편. 제출본에는 출처 이용조건이 공개 라이선스(CC BY, CC BY-SA, 퍼블릭 도메인, 미국 정부 공개 자료)로 확인된 편만 남겼습니다. 이용조건이 비어 있거나 확인되지 않은 37편(성경 개역한글판 낭독 포함)은 카탈로그와 음원에서 뺐습니다. 운영 서버에는 전체가 있습니다. 51편(`s2-`)은 대본 자동 검수 통과 뒤 Typecast로 합성했고, Whisper 받아쓰기 자동 대조를 거쳤습니다. `category`는 관심사 코드(C01–C21)이고, 출처 이용조건은 `source.license`에 있습니다.
- `outputs/gen/audio/*.mp3`: 위 이야기의 음원(128 kbps, 모노). 목소리는 대시보드에서 편마다 배정한 Typecast 화자입니다.
- `inputs/demo_snapshot/ads/*.mp3`: 광고 4편(시범 서버 카탈로그와 같은 파일). 편성에서 이야기 2편마다 들어갑니다.
- `inputs/demo_snapshot/audio`: 비어 있습니다. 이 카탈로그의 이야기는 모두 `outputs/gen/audio`를 씁니다.
- 청취 기록 DB는 포함하지 않습니다. `TEUM_EVENTS_DB`에 새 파일 경로를 지정하세요.
