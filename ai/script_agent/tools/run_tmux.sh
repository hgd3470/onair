#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="script-batch"
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$TASK_ROOT/batch_run.log"

action="${1:-help}"

case "$action" in
  start)
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "[!] 이미 '$SESSION_NAME' tmux 세션이 실행 중입니다."
      echo "    세션 확인: bash tools/run_tmux.sh attach"
      exit 1
    fi
    echo "[+] '$SESSION_NAME' tmux 세션을 백그라운드로 시작합니다..."
    tmux new-session -d -s "$SESSION_NAME" -c "$TASK_ROOT" \
      "bash tools/run.sh run --mode batch --limit 500 2>&1 | tee -a '$LOG_FILE'"
    echo "[✓] 백그라운드 실행 시작 완료! 이제 터미널이나 세션을 닫아도 계속 실행됩니다."
    echo "    - 실시간 화면 연결 : bash tools/run_tmux.sh attach"
    echo "    - 최근 로그 확인   : bash tools/run_tmux.sh logs"
    ;;
  attach)
    if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "[!] '$SESSION_NAME' 세션이 실행 중이지 않습니다."
      exit 1
    fi
    echo "[*] '$SESSION_NAME' 세션에 연결합니다. (화면에서 빠져나오기: Ctrl+B 누른 후 D)"
    tmux attach -t "$SESSION_NAME"
    ;;
  logs)
    if [[ -f "$LOG_FILE" ]]; then
      tail -f -n 50 "$LOG_FILE"
    else
      echo "[!] 로그 파일이 아직 없습니다: $LOG_FILE"
    fi
    ;;
  status)
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "[✓] '$SESSION_NAME' 세션이 정상 실행 중입니다:"
      tmux ls | grep "$SESSION_NAME"
    else
      echo "[-] '$SESSION_NAME' 세션이 실행 중이지 않습니다."
    fi
    ;;
  stop)
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      tmux kill-session -t "$SESSION_NAME"
      echo "[✓] '$SESSION_NAME' 세션을 종료했습니다."
    else
      echo "[!] 실행 중인 '$SESSION_NAME' 세션이 없습니다."
    fi
    ;;
  *)
    echo "사용법: bash tools/run_tmux.sh [start|attach|logs|status|stop]"
    echo "  start  : tmux 백그라운드 세션 생성 및 batch 모드 실행"
    echo "  attach : 실행 중인 tmux 화면으로 연결 (빠져나오기: Ctrl+B 누른 후 D)"
    echo "  logs   : 실시간 로그 스트리밍 (tail -f)"
    echo "  status : 세션 실행 여부 확인"
    echo "  stop   : tmux 세션 강제 종료"
    ;;
esac
