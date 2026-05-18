#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Kurulu master (koordinatör) paketini doğrular.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="/opt/transcription-cluster"
DATA_DIR="${TRANSCRIPTION_DATA_DIR:-/opt/transcription-data}"
LOG_DIR="/var/log/transcription"
POSTGRES_BIN="/Applications/Postgres.app/Contents/Versions/latest/bin"
FAILURES=0

pass() { echo "  ✓ $*"; }
fail() { echo "  ✗ $*"; FAILURES=$((FAILURES + 1)); }

export PATH="${POSTGRES_BIN}:${INSTALL_DIR}/venv/bin:${PATH}"

echo "=== Master Kurulum Doğrulaması ==="
echo ""

# ── launchd ───────────────────────────────────────────────────────────────────
if sudo launchctl print system/com.transcription.coordinator &>/dev/null; then
    pass "Koordinatör launchd servisi kayıtlı"
else
    fail "Koordinatör launchd servisi bulunamadı"
fi

# ── PostgreSQL ────────────────────────────────────────────────────────────────
if [ -d "/Applications/Postgres.app" ]; then
    pass "Postgres.app kurulu"
else
    fail "Postgres.app eksik"
fi

if command -v pg_isready &>/dev/null && pg_isready -h localhost -q 2>/dev/null; then
    pass "PostgreSQL çalışıyor"
else
    fail "PostgreSQL hazır değil"
fi

if psql -h localhost -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw transcription_cluster; then
    pass "transcription_cluster veritabanı mevcut"
else
    fail "transcription_cluster veritabanı eksik"
fi

# ── Python / uygulama ─────────────────────────────────────────────────────────
if [ -x "${INSTALL_DIR}/venv/bin/python3" ]; then
    pass "Python sanal ortamı mevcut"
else
    fail "Sanal ortam eksik"
fi

if [ -f "${INSTALL_DIR}/coordinator/.env" ]; then
    pass "Yapılandırma dosyası (.env) mevcut"
else
    fail ".env eksik"
fi

if [ -x "${INSTALL_DIR}/coordinator/coordinator.sh" ]; then
    pass "coordinator.sh çalıştırılabilir"
else
    fail "coordinator.sh eksik"
fi

if [ -d "${INSTALL_DIR}/coordinator/static" ]; then
    pass "Dashboard static dizini mevcut"
else
    fail "Dashboard static dizini eksik"
fi

# ── Veri dizinleri ────────────────────────────────────────────────────────────
for dir in "${DATA_DIR}/input" "${DATA_DIR}/output"; do
    if [ -d "${dir}" ]; then
        pass "Dizin mevcut: ${dir}"
    else
        fail "Dizin eksik: ${dir}"
    fi
done

# ── API ───────────────────────────────────────────────────────────────────────
PORT="$(grep -E '^COORDINATOR_PORT=' "${INSTALL_DIR}/coordinator/.env" 2>/dev/null | cut -d= -f2 || echo 8080)"
if curl -sf "http://localhost:${PORT}/healthz" > /dev/null 2>&1; then
    pass "API yanıt veriyor (port ${PORT})"
else
    echo "  ℹ API henüz yanıt vermiyor (servis başlıyor olabilir)"
fi

# ── Alembic ───────────────────────────────────────────────────────────────────
if [ -x "${INSTALL_DIR}/venv/bin/alembic" ]; then
    cd "${INSTALL_DIR}/coordinator"
    set -a
    # shellcheck disable=SC1091
    source "${INSTALL_DIR}/coordinator/.env"
    set +a
    if alembic current 2>/dev/null | grep -q .; then
        pass "Alembic migrasyon durumu okunabiliyor"
    else
        fail "Alembic migrasyon durumu okunamadı"
    fi
fi

echo ""
if [ "${FAILURES}" -eq 0 ]; then
    echo "Doğrulama başarılı."
    LOCAL_IP="$(ipconfig getifaddr en0 2>/dev/null || echo localhost)"
    echo "Dashboard: http://${LOCAL_IP}:${PORT}"
    exit 0
fi

echo "Doğrulama başarısız (${FAILURES} hata)."
echo "Log: tail -f ${LOG_DIR}/coordinator-error.log"
exit 1
