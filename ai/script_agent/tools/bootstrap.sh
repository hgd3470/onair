#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TASK_ROOT"
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
if [[ ! -x .tools/poetry/bin/poetry ]]; then
  "$PYTHON_BIN" -m venv .tools/poetry
  .tools/poetry/bin/pip install 'poetry>=2.2,<3'
fi
POETRY_VIRTUALENVS_IN_PROJECT=true .tools/poetry/bin/poetry sync --no-interaction
printf '%s\n' 'Ready. Place your keys in .env, then use: bash tools/run.sh preflight (calls model APIs).'
