#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh — Start the coordinator server
# Usage:
#   ./scripts/start.sh          — production mode (JSON logs, no reload)
#   ./scripts/start.sh --dev    — development mode (console logs, hot reload)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Activate venv if not already active
if [ -z "${VIRTUAL_ENV:-}" ] && [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Load .env if present
if [ -f ".env" ]; then
    set -o allexport
    source .env
    set +o allexport
fi

DEV_MODE=false
if [[ "${1:-}" == "--dev" ]]; then
    DEV_MODE=true
fi

# Verify migrations are up to date
echo "Migrasyon durumu kontrol ediliyor..."
alembic upgrade head

if [ "$DEV_MODE" = true ]; then
    echo "Geliştirme modunda başlatılıyor..."
    export JSON_LOGS=false
    export LOG_LEVEL=DEBUG
    export RELOAD=true
    uvicorn app.main:app \
        --host "${COORDINATOR_HOST:-0.0.0.0}" \
        --port "${COORDINATOR_PORT:-8080}" \
        --reload \
        --loop uvloop \
        --log-level debug
else
    echo "Üretim modunda başlatılıyor..."
    uvicorn app.main:app \
        --host "${COORDINATOR_HOST:-0.0.0.0}" \
        --port "${COORDINATOR_PORT:-8080}" \
        --workers 1 \
        --loop uvloop \
        --log-level info \
        --access-log
fi
