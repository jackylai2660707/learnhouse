#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
REPORT_PATH=${LEARNHOUSE_RAG_VERIFY_REPORT:-/var/lib/learnhouse/rag-verification/latest.json}
ALERT_HOOK=${LEARNHOUSE_RAG_VERIFY_ALERT_HOOK:-}

report_dir=$(dirname -- "$REPORT_PATH")
mkdir -p "$report_dir"
chmod 700 "$report_dir"
work_dir=$(mktemp -d "$report_dir/.rag-verify.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT HUP INT TERM
report_tmp="$work_dir/report.json"
error_tmp="$work_dir/stderr.log"

cd "$PROJECT_ROOT"
status=0
if docker compose exec -T learnhouse-app \
    /app/api/.venv/bin/python /app/api/scripts/reembed_courses.py --verify-only \
    >"$report_tmp" 2>"$error_tmp"; then
    status=0
else
    status=$?
fi

if [ ! -s "$report_tmp" ]; then
    printf '%s\n' '{"verification":{"healthy":false,"error_code":"verification_process_failed"}}' >"$report_tmp"
fi
chmod 600 "$report_tmp"
mv -f "$report_tmp" "$REPORT_PATH"

if [ "$status" -ne 0 ]; then
    echo "LearnHouse RAG verification failed; inspect the private report." >&2
    if [ -n "$ALERT_HOOK" ]; then
        case "$ALERT_HOOK" in
            /*) ;;
            *) echo "RAG alert hook must be an absolute path; hook skipped." >&2; exit "$status" ;;
        esac
        if [ -x "$ALERT_HOOK" ]; then
            "$ALERT_HOOK" rag_verify_failed "$REPORT_PATH" || \
                echo "LearnHouse RAG alert hook failed." >&2
        else
            echo "RAG alert hook is not executable; hook skipped." >&2
        fi
    fi
fi

exit "$status"
