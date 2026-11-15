# 자체 작성 입력·회귀 검사 예제

- `sources/`: 여섯 제작 유형의 짧은 자체 작성 연결 시험 원문.
- `reviewer_cases/`: 의도적 오류를 넣은 수기 대본. 테스트와 검토·수정 실험용이며 실제 생성 결과가 아니다.
- `feedback*.json`: 한국어 외부 검수 피드백 예시. 적용 전 대상 문장·버전을 대조한다.
- `feedback.refine.jsonl`: refine 배치 출력 형식. `S2-EXAMPLE`을 검수한 실제 content_id로 바꾸고 검수 시점의 `--expected-version`을 지정한다.
- `user-summary.meta.json`: 사용자 요약문 sidecar 구조. `example.org`는 예시 주소이며 원출처 조건은 일부러 미확인이다. 실제 수집 근거로 교체하기 전에는 hold다. 입력과 같은 이름의 `.meta.json`으로 복사한다.

외부 수집 원문·실제 생성 결과·과거 평가 출력은 포함하지 않는다.
`input/`에 예제 원문을 복사하면 처리 대상이 되고, 성공 시 해당 복사본은 `archive/`에 보존된다.
이 예제의 가상 사건·규칙을 실제 사실 자료로 사용하지 않는다.
