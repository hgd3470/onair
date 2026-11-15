# 콘텐츠 제작 파이프라인
완성 대본 → TTS 작업 → 결과 수집 → 사람 검수 → 서버 카탈로그 게시. 의존성: `requirements.txt`(numpy, soundfile, lameenc; GPU 실행기는 torch·qwen-tts 추가).
`ai/content`에서 실행하며 상태·이력은 `$WORK/pipeline.sqlite`, 대본은 항목별 script.json/text.txt에 저장한다.
게시 음원은 `<data_root>/outputs/gen/audio/<id>.vN.mp3`(128 kbps mono), URI는 `/gen/<id>.vN.mp3`이다. 검수·재생성용 WAV는 work 폴더에 보존하며 길이도 WAV 기준이다.
```sh
export PYTHONPATH="$PWD/src" WORK=/tmp/teum-work DATA=/tmp/teum-data
python -m teum_content ingest ../../content/scripts/*.md --work "$WORK"
python -m teum_content tts-jobs --work "$WORK" --out jobs.jsonl
python -m teum_content.tts_runner jobs.jsonl results.jsonl --stub
python -m teum_content tts-ingest results.jsonl --work "$WORK"
python -m teum_content review w1-c01-01 --approve --work "$WORK" # 각 항목 승인 또는 --reject
python -m teum_content publish --work "$WORK" --data-root "$DATA"
python -m teum_content status --work "$WORK"
```
GPU 서버에서는 같은 jobs/results 파일과 동일한 절대 work 경로를 사용하고 `--stub` 대신 `--model <dir>`을 준다. 무음 stub은 연결 테스트용이다. 전체 흐름은 `run-pipeline-6.sh`(`PLEOS_ROOT`·`MODEL` 환경변수).
기본 목소리는 투표로 정한 30대 남성 대화체 체크포인트(`tts-jobs` 기본 `--speaker teum_063 --language Auto`, `--model`에 체크포인트 폴더)다. 기본 화자는 단문으로 학습돼 여러 문장을 한 번에 주면 중간 문장을 건너뛰므로 문장 단위(`--chunk-chars 0`)로 합성하고, 말 속도가 비정상(초당 9글자 초과)인 문장은 `--retries`번까지 다시 합성한다. `--chunk-chars N`은 이어지는 문장을 N글자까지 묶는다(프리셋 화자처럼 긴 입력을 견디는 모델용). 쉼은 `--gap` 초(또는 `auto` = 묶음 안 문장 끝 쉼에 맞춤)를 기준으로 앞 문장의 문장부호(?·!·…·,)에 따라 늘이거나 줄인다. `run-pipeline-6.sh`는 `--chunk-chars 0 --gap 0.8 --retries 2`를 쓴다. 낭독 본문에 숫자·영문이 있으면 ingest가 "한글 읽기 확인" 경고를 기록한다.
실패는 최대 3회; 재시도마다 새 jobs/results 파일 사용. 반려본 재등록은 재생성 요청, 동일 본문 재등록은 그 외 상태 유지. 변경본은 재검수하며 이전 게시본은 유지한다. 단일 작성자 실행을 전제한다.
