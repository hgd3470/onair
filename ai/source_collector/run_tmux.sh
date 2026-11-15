#!/usr/bin/env bash
set -euo pipefail

SESSION_NAME="source-collector"
WORK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$WORK_DIR/collector_tmux.log"
export PATH="/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

action="${1:-help}"
shift || true

case "$action" in
  start)
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "[!] 이미 '$SESSION_NAME' tmux 세션이 실행 중입니다."
      echo "    세션 화면 연결: ./run_tmux.sh attach"
      exit 1
    fi
    ARGS="${*:-run --mode staging --categories all --count 20}"
    echo "[+] '$SESSION_NAME' tmux 세션을 시작합니다: ./collector $ARGS"
    tmux new-session -d -s "$SESSION_NAME" -c "$WORK_DIR" \
      "export PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin:\$PATH; ./collector $ARGS 2>&1 | tee -a '$LOG_FILE'"
    echo "[✓] 백그라운드 실행 시작 완료! SSH 연결을 끊어도 끝까지 수집·평가를 수행합니다."
    echo "    - 실시간 화면 보기 : ./run_tmux.sh attach  (빠져나오기: Ctrl+B 누른 뒤 D)"
    echo "    - 실시간 로그 보기 : ./run_tmux.sh logs"
    echo "    - 실행 상태 확인   : ./run_tmux.sh status"
    ;;
  agy)
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "[!] 이미 '$SESSION_NAME' tmux 세션이 실행 중입니다."
      exit 1
    fi
    PROMPT="${*:-세부 카테고리별로 20개씩 good 폴더에 수집해줘}"
    echo "[+] '$SESSION_NAME' tmux 세션에서 agy 에이전트를 실행합니다..."
    tmux new-session -d -s "$SESSION_NAME" -c "$WORK_DIR" \
      "export PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin:\$PATH; agy --model gemini-3.8-flash-medium --dangerously-skip-permissions -p \"$PROMPT\" 2>&1 | tee -a '$LOG_FILE'"
    echo "[✓] 백그라운드 agy 실행 시작 완료! SSH 연결을 끊어도 스스로 완료합니다."
    echo "    - 실시간 로그 보기 : ./run_tmux.sh logs"
    ;;
  attach)
    tmux attach -t "$SESSION_NAME"
    ;;
  logs)
    touch "$LOG_FILE"
    tail -f -n 50 "$LOG_FILE"
    ;;
  status)
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "[✓] '$SESSION_NAME' tmux 세션이 활발히 실행 중입니다:"
      tmux ls | grep "$SESSION_NAME"
    else
      echo "[-] 현재 실행 중인 '$SESSION_NAME' 세션이 없습니다."
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
    echo "사용법:"
    echo "  ./run_tmux.sh start [collector 인자] : 하네스 직접 백그라운드 실행 (기본: run --mode staging --categories all --count 20)"
    echo "  ./run_tmux.sh agy \"프롬프트\"         : agy 에이전트에게 자연어로 지시하여 백그라운드 실행"
    echo "  ./run_tmux.sh attach                 : 실행 중인 tmux 세션 화면 연결 (분리: Ctrl+B, D)"
    echo "  ./run_tmux.sh logs                   : 실행 로그 실시간 스트리밍"
    echo "  ./run_tmux.sh status                 : tmux 세션 동작 여부 확인"
    echo "  ./run_tmux.sh stop                   : tmux 세션 강제 종료"
    ;;
esac
