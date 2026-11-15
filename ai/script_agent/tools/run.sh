#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TASK_ROOT"
if [[ ! -x .venv/bin/script-agent ]]; then
  printf '%s\n' 'Run ./tools/bootstrap.sh first.' >&2
  exit 2
fi
exec .venv/bin/script-agent --root "$TASK_ROOT" "$@"
