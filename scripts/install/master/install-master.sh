#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Transkripsiyon Kümesi — Master (Koordinatör) Kurulum Betiği
#
# Çevrimdışı, kendi kendine yeten master paketini kurar.
# Hedef: macOS 14+ Apple Silicon (arm64)
#
# Kullanım (paket kök dizininden):
#   chmod +x install-master.sh
#   ./install-master.sh
#
# Özelleştirme:
#   TRANSCRIPTION_DATA_DIR=/Volumes/Data/transcription ./install-master.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="/opt/transcription-cluster"
DATA_DIR="${TRANSCRIPTION_DATA_DIR:-/opt/transcription-data}"
LOG_DIR="/var/log/transcription"
COORDINATOR_PLIST="/Library/LaunchDaemons/com.transcription.coordinator.plist"
PGWATCHER_PLIST="/Library/LaunchDaemons/com.transcription.pgwatcher.plist"
PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD_DIR="${PACKAGE_ROOT}/payload"
DATABASE_NAME="transcription_cluster"

echo "=== Transkripsiyon Kümesi Master Kurulumu ==="
echo ""

# ── 1. Gereksinimler ─────────────────────────────────────────────────────────
echo "[1/9] Gereksinimler kontrol ediliyor..."

if [ ! -d "${PAYLOAD_DIR}" ]; then
    echo "HATA: payload/ dizini bulunamadı. Betiği paket kök dizininden çalıştırın."
    exit 1
fi

if [ "$(uname -m)" != "arm64" ]; then
    echo "HATA: Apple Silicon (arm64) gereklidir. Mevcut: $(uname -m)"
    exit 1
fi

OS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if [ "${OS_MAJOR}" -lt 14 ]; then
    echo "HATA: macOS 14 (Sonoma) veya üzeri gereklidir."
    exit 1
fi

PYTHON=""
for candidate in python3.12 python3.11; do
    if command -v "${candidate}" &>/dev/null; then
        minor="$("${candidate}" -c 'import sys; print(sys.version_info.minor)')"
        major="$("${candidate}" -c 'import sys; print(sys.version_info.major)')"
        if [ "${major}" -eq 3 ] && [ "${minor}" -ge 11 ]; then
            PYTHON="${candidate}"
            break
        fi
    fi
done

if [ -z "${PYTHON}" ]; then
    PYTHON_PKG="$(find "${PAYLOAD_DIR}/python" -maxdepth 1 -name 'python-3.12*.pkg' 2>/dev/null | head -n 1 || true)"
    if [ -n "${PYTHON_PKG}" ] && [ -f "${PYTHON_PKG}" ]; then
        echo "  Python 3.12 paket içinden kuruluyor..."
        sudo installer -pkg "${PYTHON_PKG}" -target /
        PYTHON="python3.12"
    else
        echo "HATA: Python 3.11+ bulunamadı."
        echo "  payload/python/ altına python-3.12.x-macos14-arm64.pkg ekleyin."
        exit 1
    fi
fi

echo "  ✓ macOS $(sw_vers -productVersion) (arm64)"
echo "  ✓ Python $("${PYTHON}" --version 2>&1)"

# ── 2. Dizin yapısı ──────────────────────────────────────────────────────────
echo "[2/9] Dizin yapısı oluşturuluyor..."
sudo mkdir -p "${INSTALL_DIR}"/{coordinator,venv}
sudo mkdir -p "${DATA_DIR}"/{input,output}
sudo mkdir -p "${LOG_DIR}"
sudo chown -R "$(whoami)":"$(id -gn)" "${INSTALL_DIR}" "${DATA_DIR}" "${LOG_DIR}"

# ── 3. PostgreSQL ────────────────────────────────────────────────────────────
echo "[3/9] PostgreSQL kurulumu ve başlatma..."
export PACKAGE_ROOT PAYLOAD_DIR
bash "${PAYLOAD_DIR}/scripts/init-postgres.sh"

POSTGRES_BIN="/Applications/Postgres.app/Contents/Versions/latest/bin"
export PATH="${POSTGRES_BIN}:${PATH}"

# ── 4. Python sanal ortamı ───────────────────────────────────────────────────
echo "[4/9] Python sanal ortamı oluşturuluyor..."
"${PYTHON}" -m venv "${INSTALL_DIR}/venv"
# shellcheck disable=SC1091
source "${INSTALL_DIR}/venv/bin/activate"

if [ ! -d "${PAYLOAD_DIR}/wheelhouse" ]; then
    echo "HATA: payload/wheelhouse eksik."
    exit 1
fi

pip install --upgrade pip wheel setuptools --quiet
pip install --no-index --find-links="${PAYLOAD_DIR}/wheelhouse" \
    -r "${PAYLOAD_DIR}/coordinator/requirements.txt" --quiet
echo "  ✓ Python bağımlılıkları yüklendi (çevrimdışı)"

# ── 5. Uygulama dosyaları ────────────────────────────────────────────────────
echo "[5/9] Koordinatör ve dashboard kopyalanıyor..."
rsync -a \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='tests' \
    --exclude='packaging/postgres' \
    "${PAYLOAD_DIR}/coordinator/" "${INSTALL_DIR}/coordinator/"

if [ -d "${PAYLOAD_DIR}/dashboard/dist" ]; then
    rm -rf "${INSTALL_DIR}/coordinator/static"
    mkdir -p "${INSTALL_DIR}/coordinator/static"
    cp -R "${PAYLOAD_DIR}/dashboard/dist/." "${INSTALL_DIR}/coordinator/static/"
    echo "  ✓ Dashboard static dosyaları kopyalandı"
else
    echo "  UYARI: Dashboard derlemesi bulunamadı (payload/dashboard/dist)"
    mkdir -p "${INSTALL_DIR}/coordinator/static"
fi

chmod +x "${INSTALL_DIR}/coordinator/coordinator.sh"
chmod +x "${INSTALL_DIR}/coordinator/scripts/wait-postgres.sh" 2>/dev/null || true

# Paket betiklerini kurulum dizinine kopyala (pgwatcher / coordinator erişimi)
mkdir -p "${INSTALL_DIR}/coordinator/scripts"
for script in init-postgres.sh bootstrap-master-config.sh ensure-postgres.sh; do
    if [ -f "${PAYLOAD_DIR}/scripts/${script}" ]; then
        cp "${PAYLOAD_DIR}/scripts/${script}" "${INSTALL_DIR}/coordinator/scripts/"
    fi
done
chmod +x "${INSTALL_DIR}/coordinator/scripts/"*.sh 2>/dev/null || true

# ── 6. Yapılandırma ──────────────────────────────────────────────────────────
echo "[6/9] Yapılandırma oluşturuluyor..."
export INSTALL_DIR DATA_DIR LOG_DIR DATABASE_NAME
bash "${PAYLOAD_DIR}/scripts/bootstrap-master-config.sh"

# ── 7. Veritabanı şeması ─────────────────────────────────────────────────────
echo "[7/9] Veritabanı şeması uygulanıyor..."
cd "${INSTALL_DIR}/coordinator"
set -a
# shellcheck disable=SC1091
source "${INSTALL_DIR}/coordinator/.env"
set +a
alembic upgrade head
echo "  ✓ Alembic migrasyonları tamamlandı"

# ── 8. launchd — PostgreSQL izleyici ───────────────────────────────────────
echo "[8/9] Sistem servisleri kuruluyor..."

PGWATCHER_SRC="${PAYLOAD_DIR}/launchd/com.transcription.pgwatcher.plist"
if [ -f "${PGWATCHER_SRC}" ]; then
    sed \
        -e "s|INSTALL_DIR|${INSTALL_DIR}|g" \
        -e "s|POSTGRES_BIN|${POSTGRES_BIN}|g" \
        "${PGWATCHER_SRC}" | sudo tee "${PGWATCHER_PLIST}" > /dev/null
    sudo launchctl bootout system "${PGWATCHER_PLIST}" 2>/dev/null || true
    sudo launchctl bootstrap system "${PGWATCHER_PLIST}"
    echo "  ✓ PostgreSQL izleyici servisi kuruldu"
fi

# ── 9. launchd — Koordinatör ─────────────────────────────────────────────────
COORD_PLIST_SRC="${PAYLOAD_DIR}/launchd/com.transcription.coordinator.plist"
if [ ! -f "${COORD_PLIST_SRC}" ]; then
    echo "HATA: launchd plist eksik."
    exit 1
fi

sed \
    -e "s|INSTALL_DIR|${INSTALL_DIR}|g" \
    -e "s|LOG_DIR|${LOG_DIR}|g" \
    -e "s|POSTGRES_BIN|${POSTGRES_BIN}|g" \
    "${COORD_PLIST_SRC}" | sudo tee "${COORDINATOR_PLIST}" > /dev/null

sudo launchctl bootout system "${COORDINATOR_PLIST}" 2>/dev/null || true
sudo launchctl bootstrap system "${COORDINATOR_PLIST}"

LOCAL_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "localhost")"

echo ""
echo "=== Master Kurulumu Tamamlandı! ==="
echo ""
echo "Dashboard     : http://${LOCAL_IP}:8080"
echo "Giriş dizini  : ${DATA_DIR}/input"
echo "Çıktı dizini  : ${DATA_DIR}/output"
echo "Yapılandırma  : ${INSTALL_DIR}/coordinator/.env"
echo ""
echo "Servis durumu : sudo launchctl list com.transcription.coordinator"
echo "Loglar        : tail -f ${LOG_DIR}/coordinator.log"
echo ""
echo "Manuel başlatma: ${INSTALL_DIR}/coordinator/coordinator.sh"
echo "Doğrulama     : ./verify-master-install.sh"
