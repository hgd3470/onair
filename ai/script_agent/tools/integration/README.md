# one-server 통합 호환성 검사 · 2026-11-13

## 최신 검증

PR #23의 생성기·수집기(`ca4c27a`)와 PR #25의 통합부(`0ba7bbe`)를 별도 사본에서 병합해 확인했다. Git 충돌은 없었고 이 디렉터리의 통합 수용 검사 **6개가 모두 통과**했다. 아래 결과는 결정적 모델 대역과 로컬 Studio HTTP를 사용한 코드 검증이다. 실제 모델·TTS·운영 서버 배포나 공개 승인은 수행하지 않았다.

| 검사 | 결과 |
|---|---|
| output/<batch>/ 및 output/_unbatched/ 탐색 | 실제 생성 큐 결과 제출 확인 |
| Studio 업로드·검수 ZIP·검수기 입력 | JSON·내장 원문·라이선스·출처 메타데이터 보존 확인 |
| 내부/외부 수정 횟수 | 내부 2회 이후 외부 2회, version=1.0.4 확인 |
| 공개 전 처리 검토 대기 | pending_processing_review 결과의 자동 게시 차단 확인 |
| 외부 수정 후 hold | 최신 수정본을 Studio에 전달하고 사람 확인으로 전환 확인 |
| 입력함이 빈 배치 재개 | processing의 미완료 작업이 있으면 생성기 호출 확인 |
| 최신 팩토리·인프라 검사 | 14개 통과. 별도 음원 게시 검사 1개는 ffmpeg 미설치로 실패 |

음원 게시 검사의 실패 원인은 `FileNotFoundError: ffmpeg`다. 이 환경에서 해당 경로를 검증 완료로 표시하지 않는다. 실제 Typecast·받아쓰기 모델·운영 권한도 별도 검증 대상이다.

## PR #26 추가 검증

이 브랜치는 기존 6개 계약에 팀 권리 유보 입력의 출력 탐색·Studio 왕복 2개를 더해 **8개 모두 통과**했다. `All rights reserved`와 `© 틈(TEUM) 팀. All rights reserved.`가 생성 JSON, Studio 검수 ZIP 및 내장 입력 메타데이터에 유지되는지 확인한다. 과거 CC0 입력도 별도로 검사한다. 기준 생성기·수집기 회귀는 script_agent 293개, issue_collector 196개, source_collector 62개로 **551개 통과**했다.

## PR별 계약

- PR #23은 Gemini v22 생성기·배치 복구와 AI 창작 사연의 허위 출처 제거를 담당한다.
- PR #26은 #23을 기준으로 기사 전문 인계·v27 재서술·근거 있는 언론사 인용·팀 저작권 정책을 추가한다. 이슈 대본과 자체 작성 사실 자료의 새 공개 조건은 #26의 `All rights reserved`를 따른다. #23의 CC0 입력 검사와 아래 호환 사례는 과거 입력 재현용이며 새 콘텐츠의 공개 정책을 뜻하지 않는다.
- PR #25는 팩토리·Studio·운영 실행을 담당한다. 이번 검증은 해당 브랜치를 시험 사본에만 합쳤으며 #23이나 #26에 서버 구현을 끌어오지 않았다.
- TTS PR #3은 이번 변경 파일과 겹치지 않는다.

`ready_for_downstream_review`는 사람 승인이나 공개 승인이 아니다. 소비자는 최신 대본·release_status·검토 상태를 확인해야 한다. 원기사 이용 조건과 처리 권한은 팀의 콘텐츠 권리 표기와 별도로 유지한다.

최신 one-server 초기 모델은 Gemini/OpenAI/Claude이며, 기존 teum.env의 명시적 설정은 계속 우선한다. 저장소 설정 변경만으로 운영 환경의 모델이 바뀌지는 않는다.

## 재현 방법

두 PR을 병합한 별도 사본의 저장소 루트에서 실행한다. 운영 데이터 폴더에서 실행하지 않는다.

```bash
python3 -m venv /tmp/teum-integration-venv
/tmp/teum-integration-venv/bin/pip install -r infra/one-server/requirements.txt
/tmp/teum-integration-venv/bin/pip install -e ai/script_agent

TEUM_GENERATOR=gemini TEUM_REVIEWER=openai TEUM_REFINE=claude \
PYTHONPATH=ai/factory/src:ai/factory/tests:ai/script_agent/src:ai/script_agent/tests:ai/refine/src \
/tmp/teum-integration-venv/bin/python -m pytest \
  ai/script_agent/tools/integration/one_server_contract.py -q

# ffmpeg 설치 후 음원 게시 검사까지 포함해 실행
TEUM_GENERATOR=gemini TEUM_REVIEWER=openai TEUM_REFINE=claude \
PYTHONPATH=ai/factory/src:ai/script_agent/src:ai/refine/src \
/tmp/teum-integration-venv/bin/python -m pytest ai/factory/tests infra/one-server/tests -q
```

이 통합 검사는 명시적으로 지정할 때만 실행되며 생성기 기본 테스트 목록에 포함되지 않는다.

## 과거 결과

2026-11-12의 one-server `d87e389`에서는 6개 중 2개 통과·4개 실패였다. 당시 출력 탐색·게시 조건·hold 수정본 업로드·빈 입력함 재개 문제가 최신 `0ba7bbe`에서 해결됐다. 이전 결과는 당시 검증 문서에 보존돼 있다.
