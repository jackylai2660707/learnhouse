#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

exec uv run --project "$PROJECT_ROOT/apps/api" \
  python "$PROJECT_ROOT/apps/api/scripts/production_restore_verify.py" "$@"
