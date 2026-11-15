# 제작 관리 사이트 (studio)

팀원이 대본, 검수 판정, 목소리, 음원, 게시를 한 화면에서 다루는 사이트다. `/admin`이 제작 관리, `/app`이 청취 웹앱이고 `/api/v1`은 API 서버(`server/teum`)로 넘긴다.

| 파일 | 하는 일 |
|---|---|
| `teum_gate.py` | 로그인과 경로 나누기. `uvicorn teum_gate:app`으로 띄운다 |
| `teum_admin.py` | 제작 관리 화면의 API. 대본 올리기, 검수 판정 반영, 목소리 배정, Typecast 합성, 받아쓰기 대조, 게시 |
| `pipeline_ops.py` | 대본 한 편을 `ai/content` 제작 파이프라인에 넣고, 음원을 붙이고, 게시한다 |
| `typecast_client.py` | Typecast API 호출 |
| `teum_stt.py` | 합성 음원을 Whisper로 받아써 대본과 대조한다 (GPU, `requirements-gpu.txt`) |
| `admin_users.py` | 팀원 계정과 비밀번호 |
| `seed.json`, `voices.json` | 처음 띄울 때 넣는 목소리 10종, 원천 목록 |

데이터는 모두 `TEUM_HOME` 아래에 둔다. 실행은 [infra/one-server](../../infra/one-server/README.md)를 따른다.
