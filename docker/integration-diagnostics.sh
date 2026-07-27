#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)

# Production endpoints use Compose DNS names (for example `embeddings` and
# `executor`). Run the active diagnostic from the already-running app service
# so the documented operator command tests the same network and configuration
# as real requests. Building or starting services here would make a diagnostic
# unexpectedly disruptive.
report_path=""
container_args=()
while (($#)); do
  case "$1" in
    --report)
      if (($# < 2)); then
        echo "integration diagnostics: --report requires a path" >&2
        exit 2
      fi
      report_path=$2
      shift 2
      ;;
    --report=*)
      report_path=${1#--report=}
      shift
      ;;
    *)
      container_args+=("$1")
      shift
      ;;
  esac
done

cd "$PROJECT_ROOT"

if ! docker compose ps --status running --services | grep -Fxq learnhouse-app; then
  echo "integration diagnostics: learnhouse-app is not running" >&2
  exit 2
fi

umask 077
output_file=$(mktemp)
trap 'rm -f -- "$output_file"' EXIT

set +e
docker compose exec -T learnhouse-app sh -eu -c \
  'cd /app/api && exec .venv/bin/python -m scripts.integration_diagnostics "$@"' \
  sh "${container_args[@]}" >"$output_file"
diagnostic_status=$?
set -e

cat "$output_file"

if [[ -n "$report_path" ]] && python3 -m json.tool "$output_file" >/dev/null 2>&1; then
  report_dir=$(dirname -- "$report_path")
  install -d -m 700 -- "$report_dir"
  install -m 600 -- "$output_file" "$report_path"
fi

exit "$diagnostic_status"
