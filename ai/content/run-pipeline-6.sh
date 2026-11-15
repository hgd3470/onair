#!/usr/bin/env bash
# 대본 6편 → TTS → 결과 수집. GPU 서버에서 실행.
# 사용: PLEOS_ROOT=<작업 폴더> MODEL=<화자 체크포인트 폴더, 예: models/teum-063-friend> bash run-pipeline-6.sh [speaker]
set -euo pipefail
: "${PLEOS_ROOT:?작업 폴더(.venv, outputs/ 가 있는 곳)}" "${MODEL:?화자 체크포인트 폴더(CustomVoice 형식)}"
SPK=${1:-teum_063}
HERE=$(cd "$(dirname "$0")" && pwd)
source "$PLEOS_ROOT/.venv/bin/activate"
cd "$HERE"; export PYTHONPATH=$PWD/src
WORK=$PLEOS_ROOT/outputs/work; RUN=$PLEOS_ROOT/outputs/runs/$(date +%Y%m%d-%H%M%S); mkdir -p "$RUN" "$WORK"
python -m teum_content ingest ../../content/scripts/*.md --work "$WORK"
python -m teum_content tts-jobs --work "$WORK" --out "$RUN/jobs.jsonl" --speaker "$SPK" --language Auto
echo "jobs: $(wc -l < "$RUN/jobs.jsonl")"
python -m teum_content.tts_runner "$RUN/jobs.jsonl" "$RUN/results.jsonl" --model "$MODEL" --chunk-chars 0 --gap 0.8 --retries 2 2> "$RUN/runner.stderr" || true
python -m teum_content tts-ingest "$RUN/results.jsonl" --work "$WORK"
python -m teum_content status --work "$WORK"
echo "run dir: $RUN"
