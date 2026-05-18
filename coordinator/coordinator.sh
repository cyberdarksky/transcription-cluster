#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Transkripsiyon Koordinatör — Başlatma Sarıcısı
#
# Kurulum sonrası: /opt/transcription-cluster/coordinator/coordinator.sh
# launchd veya manuel çalıştırma için ortamı hazırlar, PostgreSQL'i bekler,
# migrasyonları uygular ve uvicorn'u başlatır.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COORDINATOR_DIR="${INSTALL_ROOT}/coordinator"
VENV_DIR="${INSTALL_ROOT}/venv"
POSTGRES_BIN="${POSTGRES_BIN:-/Applications/Postgres.app/Contents/Versions/latest/bin}"

export PATH="${POSTGRES_BIN}:${VENV_DIR}/bin:${PATH}"

if [ -f "${COORDINATOR_DIR}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${COORDINATOR_DIR}/.env"
    set +a
fi

cd "${COORDINATOR_DIR}"

if [ ! -x "${VENV_DIR}/bin/python3" ]; then
    echo "HATA: Sanal ortam bulunamadı: ${VENV_DIR}" >&2
    echo "Önce install-master.sh çalıştırın." >&2
    exit 1
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# PostgreSQL hazır olana kadar bekle
WAIT_SCRIPT="${COORDINATOR_DIR}/scripts/wait-postgres.sh"
if [ -x "${WAIT_SCRIPT}" ]; then
    bash "${WAIT_SCRIPT}"
elif command -v pg_isready &>/dev/null; then
    echo "PostgreSQL bekleniyor..."
    for _ in $(seq 1 60); do
        if pg_isready -h localhost -q 2>/dev/null; then
            break
        fi
        sleep 2
    done
    pg_isready -h localhost -q || {
        echo "HATA: PostgreSQL hazır değil." >&2
        exit 1
    }
fi

echo "Migrasyonlar uygulanıyor..."
alembic upgrade head

DEV_MODE=false
if [[ "${1:-}" == "--dev" ]]; then
    DEV_MODE=true
    shift
fi

HOST="${COORDINATOR_HOST:-0.0.0.0}"
PORT="${COORDINATOR_PORT:-8080}"

if [ "${DEV_MODE}" = true ]; then
    export JSON_LOGS=false
    export LOG_LEVEL="${LOG_LEVEL:-DEBUG}"
    export RELOAD=true
    exec uvicorn app.main:app \
        --host "${HOST}" \
        --port "${PORT}" \
        --reload \
        --loop uvloop \
        --log-level debug \
        "$@"
fi

exec uvicorn app.main:app \
    --host "${HOST}" \
    --port "${PORT}" \
    --workers 1 \
    --loop uvloop \
    --log-level info \
    --access-log \
    "$@"
