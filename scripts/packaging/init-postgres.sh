#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL kurulumu ve transcription_cluster veritabanı başlatma.
#
# Ortam değişkenleri:
#   POSTGRES_APP_SRC   — Paket içi Postgres.app yolu (varsayılan: payload/postgres/Postgres.app)
#   POSTGRES_INSTALL   — Hedef kurulum (/Applications/Postgres.app)
#   POSTGRES_DB_NAME   — Oluşturulacak veritabanı (varsayılan: transcription_cluster)
#   PACKAGE_ROOT       — Paket kök dizini (install-master.sh tarafından ayarlanır)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD_DIR="${PAYLOAD_DIR:-$(dirname "${SCRIPT_DIR}")}"
PACKAGE_ROOT="${PACKAGE_ROOT:-$(dirname "${PAYLOAD_DIR}")}"
POSTGRES_APP_SRC="${POSTGRES_APP_SRC:-${PAYLOAD_DIR}/postgres/Postgres.app}"
POSTGRES_INSTALL="${POSTGRES_INSTALL:-/Applications/Postgres.app}"
POSTGRES_BIN="${POSTGRES_INSTALL}/Contents/Versions/latest/bin"
POSTGRES_DB_NAME="${POSTGRES_DB_NAME:-transcription_cluster}"
POSTGRES_WAIT_SECONDS="${POSTGRES_WAIT_SECONDS:-180}"

export PATH="${POSTGRES_BIN}:${PATH}"

install_postgres_app() {
    if [ -d "${POSTGRES_INSTALL}" ]; then
        echo "  ℹ Postgres.app zaten kurulu: ${POSTGRES_INSTALL}"
        return 0
    fi

    if [ ! -d "${POSTGRES_APP_SRC}" ]; then
        echo "HATA: Postgres.app pakette bulunamadı: ${POSTGRES_APP_SRC}" >&2
        echo "  coordinator/packaging/postgres/Postgres.app kopyalayın veya paketi yeniden oluşturun." >&2
        exit 1
    fi

    echo "  Postgres.app kopyalanıyor..."
    cp -R "${POSTGRES_APP_SRC}" "${POSTGRES_INSTALL}"
    xattr -rd com.apple.quarantine "${POSTGRES_INSTALL}" 2>/dev/null || true
    echo "  ✓ Postgres.app kuruldu"
}

start_postgres() {
    if pg_isready -h localhost -q 2>/dev/null; then
        echo "  ✓ PostgreSQL zaten çalışıyor"
        return 0
    fi

    echo "  Postgres.app başlatılıyor..."
    open -a Postgres 2>/dev/null || open "${POSTGRES_INSTALL}" 2>/dev/null || true

    elapsed=0
    while [ "${elapsed}" -lt "${POSTGRES_WAIT_SECONDS}" ]; do
        if pg_isready -h localhost -q 2>/dev/null; then
            echo "  ✓ PostgreSQL hazır"
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    echo "HATA: PostgreSQL ${POSTGRES_WAIT_SECONDS}s içinde başlamadı." >&2
    exit 1
}

create_database() {
    if psql -h localhost -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw "${POSTGRES_DB_NAME}"; then
        echo "  ℹ Veritabanı zaten mevcut: ${POSTGRES_DB_NAME}"
    else
        createdb -h localhost "${POSTGRES_DB_NAME}" 2>/dev/null || createdb "${POSTGRES_DB_NAME}"
        echo "  ✓ Veritabanı oluşturuldu: ${POSTGRES_DB_NAME}"
    fi
}

write_version_file() {
    local version_file="${PAYLOAD_DIR}/postgres/postgres-version.txt"
    if [ -f "${version_file}" ]; then
        echo "  PostgreSQL: $(cat "${version_file}")"
    elif [ -x "${POSTGRES_BIN}/postgres" ]; then
        echo "  PostgreSQL: $("${POSTGRES_BIN}/postgres" --version)"
    fi
}

echo "PostgreSQL kurulumu..."
install_postgres_app
start_postgres
create_database
write_version_file

# Kurulum sonrası kullanım için PATH ipucu
if ! grep -q "Postgres.app" "${HOME}/.zprofile" 2>/dev/null; then
    echo "export PATH=\"${POSTGRES_BIN}:\$PATH\"" >> "${HOME}/.zprofile"
    echo "  ℹ PATH ~/.zprofile dosyasına eklendi"
fi
