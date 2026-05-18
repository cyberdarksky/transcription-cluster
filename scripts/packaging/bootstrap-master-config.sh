#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Koordinatör .env yapılandırmasını oluşturur veya günceller.
#
# Ortam değişkenleri (install-master.sh tarafından geçirilir):
#   INSTALL_DIR, DATA_DIR, LOG_DIR, COORDINATOR_PORT, DATABASE_NAME
#   FORCE_OVERWRITE=1  — mevcut .env üzerine yazar
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/transcription-cluster}"
DATA_DIR="${DATA_DIR:-/opt/transcription-data}"
LOG_DIR="${LOG_DIR:-/var/log/transcription}"
COORDINATOR_PORT="${COORDINATOR_PORT:-8080}"
DATABASE_NAME="${DATABASE_NAME:-transcription_cluster}"
WHISPER_MODEL_PATH="${WHISPER_MODEL_PATH:-/opt/transcription-models/whisper-medium-mlx}"
FORCE_OVERWRITE="${FORCE_OVERWRITE:-0}"

ENV_FILE="${INSTALL_DIR}/coordinator/.env"
STATIC_DIR="${INSTALL_DIR}/coordinator/static"
DATABASE_URL="postgresql+asyncpg://localhost/${DATABASE_NAME}"

mkdir -p "${DATA_DIR}/input" "${DATA_DIR}/output" "${LOG_DIR}"

if [ -f "${ENV_FILE}" ] && [ "${FORCE_OVERWRITE}" != "1" ]; then
    echo "  ℹ ${ENV_FILE} zaten mevcut — korunuyor (üzerine yazmak için FORCE_OVERWRITE=1)"
    exit 0
fi

cat > "${ENV_FILE}" << EOF
# Transkripsiyon Kümesi Koordinatör — üretim yapılandırması
# Oluşturulma: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

# PostgreSQL
DATABASE_URL=${DATABASE_URL}

# Sunucu
COORDINATOR_HOST=0.0.0.0
COORDINATOR_PORT=${COORDINATOR_PORT}

# Dosya depolama
INPUT_BASE_DIR=${DATA_DIR}/input
OUTPUT_BASE_DIR=${DATA_DIR}/output
STATIC_DIR=${STATIC_DIR}

# Günlük
LOG_LEVEL=INFO
JSON_LOGS=true

# İşçi izleme
WORKER_HEARTBEAT_TIMEOUT_SECONDS=90
RECOVERY_GRACE_SECONDS=30

# Kiralama ve yeniden deneme
JOB_LEASE_DURATION_SECONDS=300
LEASE_RECOVERY_INTERVAL_SECONDS=30
RETRY_SCHEDULER_INTERVAL_SECONDS=30
MAX_RETRIES_DEFAULT=3

# Bakım
WORKER_METRICS_RETENTION_DAYS=7
JOB_EVENTS_RETENTION_DAYS=90

# Whisper model yolu (işçi makinelerinde)
WHISPER_MODEL_PATH=${WHISPER_MODEL_PATH}

# Meta
COORDINATOR_VERSION=1.0.0
SERVICE_NAME=TranscriptionCluster
DB_ECHO=false
RELOAD=false
EOF

chmod 600 "${ENV_FILE}" 2>/dev/null || true
echo "  ✓ Yapılandırma oluşturuldu: ${ENV_FILE}"
