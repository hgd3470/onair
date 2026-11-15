#!/usr/bin/env bash
# One command for factory's TEUM_COLLECT_CMD; works from any working directory.
set -euo pipefail
COLLECTOR_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$COLLECTOR_ROOT/tools/run.sh" run "$@"
