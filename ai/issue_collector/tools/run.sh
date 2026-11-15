#!/usr/bin/env bash
set -euo pipefail
COLLECTOR_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
COLLECTOR_PYTHON="${ISSUE_COLLECTOR_PYTHON:-$COLLECTOR_ROOT/.venv/bin/python}"
if [[ ! -x "$COLLECTOR_PYTHON" ]]; then
  echo "Install this collector first: python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi
export PYTHONPATH="$COLLECTOR_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
# Optional legacy integration commands require a separately installed generator.
case "${1:-}" in
  demo|assess-use|publish-license)
    export PYTHONPATH="$COLLECTOR_ROOT/../script_agent/src:$PYTHONPATH" ;;
esac
exec "$COLLECTOR_PYTHON" -m issue_collector.cli --root "$COLLECTOR_ROOT" "$@"
