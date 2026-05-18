#!/usr/bin/env bash
# PostgreSQL'in hazır olmasını bekler (coordinator.sh tarafından çağrılır).
set -euo pipefail

POSTGRES_BIN="${POSTGRES_BIN:-/Applications/Postgres.app/Contents/Versions/latest/bin}"
export PATH="${POSTGRES_BIN}:${PATH}"

MAX_WAIT="${POSTGRES_WAIT_SECONDS:-120}"
INTERVAL="${POSTGRES_WAIT_INTERVAL:-2}"

if ! command -v pg_isready &>/dev/null; then
    echo "HATA: pg_isready bulunamadı. Postgres.app kurulu mu?" >&2
    exit 1
fi

echo "PostgreSQL bekleniyor (en fazla ${MAX_WAIT}s)..."
elapsed=0
while [ "${elapsed}" -lt "${MAX_WAIT}" ]; do
    if pg_isready -h localhost -q 2>/dev/null; then
        echo "  ✓ PostgreSQL hazır"
        exit 0
    fi
    sleep "${INTERVAL}"
    elapsed=$((elapsed + INTERVAL))
done

echo "HATA: PostgreSQL ${MAX_WAIT}s içinde hazır olmadı." >&2
echo "  Postgres.app çalışıyor mu? Menü çubuğundan kontrol edin." >&2
exit 1
