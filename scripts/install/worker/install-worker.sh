#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Transkripsiyon Kümesi — İşçi Kurulum Betiği
#
# Çevrimdışı, kendi kendine yeten işçi paketini kurar.
# Hedef: macOS 14+ Apple Silicon (arm64)
#
# Kullanım (paket kök dizininden):
#   chmod +x install-worker.sh
#   ./install-worker.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="/opt/transcription-worker"
MODEL_DIR="/opt/transcription-models"
LOG_DIR="/var/log/transcription-worker"
LAUNCHD_PLIST="/Library/LaunchDaemons/com.transcription.worker.plist"
PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD_DIR="${PACKAGE_ROOT}/payload"
CONFIGURE_ONLY=false

for arg in "$@"; do
    case "${arg}" in
        --configure-coordinator) CONFIGURE_ONLY=true ;;
    esac
done

_resolve_coordinator_host() {
    if [ -n "${INSTALL_COORDINATOR_HOST:-}" ]; then
        echo "${INSTALL_COORDINATOR_HOST}"
        return 0
    fi
    if [ -f "${PACKAGE_ROOT}/coordinator-host.txt" ]; then
        tr -d '[:space:]' < "${PACKAGE_ROOT}/coordinator-host.txt"
        return 0
    fi
    return 1
}

_apply_coordinator_host() {
    local host="$1"
    local env_file="${INSTALL_DIR}/worker/.env"
    if [ ! -f "${env_file}" ]; then
        echo "HATA: ${env_file} bulunamadı — önce kurulum yapın." >&2
        exit 1
    fi
    if grep -q '^COORDINATOR_HOST=' "${env_file}"; then
        sed -i '' "s|^COORDINATOR_HOST=.*|COORDINATOR_HOST=${host}|" "${env_file}"
    elif grep -q '^# COORDINATOR_HOST=' "${env_file}"; then
        sed -i '' "s|^# COORDINATOR_HOST=.*|COORDINATOR_HOST=${host}|" "${env_file}"
    else
        printf '\nCOORDINATOR_HOST=%s\n' "${host}" >> "${env_file}"
    fi
    echo "  ✓ COORDINATOR_HOST=${host} (${env_file})"
}

if [ "${CONFIGURE_ONLY}" = true ]; then
    INSTALL_DIR="/opt/transcription-worker"
    COORD_HOST="$(_resolve_coordinator_host || true)"
    if [ -z "${COORD_HOST}" ]; then
        echo "HATA: coordinator-host.txt veya INSTALL_COORDINATOR_HOST gerekli." >&2
        exit 1
    fi
    echo "=== İşçi koordinatör adresi güncelleniyor ==="
    _apply_coordinator_host "${COORD_HOST}"
    # Güncel discovery.py (varsa paketten kopyala)
    if [ -f "${PAYLOAD_DIR}/worker/agent/discovery.py" ]; then
        sudo cp "${PAYLOAD_DIR}/worker/agent/discovery.py" "${INSTALL_DIR}/worker/agent/discovery.py"
        echo "  ✓ discovery.py güncellendi"
    fi
    sudo launchctl kickstart -k system/com.transcription.worker 2>/dev/null || true
    echo "Servis yeniden başlatıldı."
    exit 0
fi

echo "=== Transkripsiyon Kümesi İşçi Kurulumu ==="
echo ""

# ── 1. Gereksinimler ─────────────────────────────────────────────────────────
echo "[1/8] Gereksinimler kontrol ediliyor..."

if [ ! -d "${PAYLOAD_DIR}" ]; then
    echo "HATA: payload/ dizini bulunamadı. Betiği paket kök dizininden çalıştırın."
    exit 1
fi

ARCH="$(uname -m)"
if [ "${ARCH}" != "arm64" ]; then
    echo "HATA: Apple Silicon (arm64) gereklidir. Mevcut: ${ARCH}"
    exit 1
fi

OS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if [ "${OS_MAJOR}" -lt 14 ]; then
    echo "HATA: macOS 14 (Sonoma) veya üzeri gereklidir."
    exit 1
fi

# Python 3.11 (MLX / mlx-whisper uyumluluğu)
# shellcheck disable=SC1091
source "${PAYLOAD_DIR}/scripts/lib/python311.sh"
require_python311 "${PAYLOAD_DIR}/python"

echo "  ✓ macOS $(sw_vers -productVersion) (${ARCH})"
echo "  ✓ Python $("${PYTHON}" --version 2>&1)"

# ── 2. Dizin yapısı ──────────────────────────────────────────────────────────
echo "[2/8] Dizin yapısı oluşturuluyor..."
sudo mkdir -p "${INSTALL_DIR}"/{worker,venv,bin,scripts/lib}
sudo mkdir -p "${MODEL_DIR}"
sudo mkdir -p "${LOG_DIR}"
sudo mkdir -p /tmp/transcription-jobs
sudo chown -R "$(whoami)":"$(id -gn)" "${INSTALL_DIR}" "${MODEL_DIR}" "${LOG_DIR}"

# ── 3. ffmpeg / ffprobe ──────────────────────────────────────────────────────
echo "[3/8] ffmpeg araçları kuruluyor..."
if [ -x "${PAYLOAD_DIR}/bin/ffmpeg" ] && [ -x "${PAYLOAD_DIR}/bin/ffprobe" ]; then
    install -m 755 "${PAYLOAD_DIR}/bin/ffmpeg" "${INSTALL_DIR}/bin/ffmpeg"
    install -m 755 "${PAYLOAD_DIR}/bin/ffprobe" "${INSTALL_DIR}/bin/ffprobe"
    xattr -d com.apple.quarantine "${INSTALL_DIR}/bin/ffmpeg" 2>/dev/null || true
    xattr -d com.apple.quarantine "${INSTALL_DIR}/bin/ffprobe" 2>/dev/null || true
    echo "  ✓ ffmpeg $("${INSTALL_DIR}/bin/ffmpeg" -version | head -n 1 | awk '{print $3}')"
else
    echo "HATA: Paket içinde ffmpeg/ffprobe eksik."
    echo "  Paketi scripts/build_worker_package.sh ile yeniden oluşturun."
    exit 1
fi

sudo cp "${PAYLOAD_DIR}/scripts/lib/python311.sh" "${INSTALL_DIR}/scripts/lib/"
sudo chmod +x "${INSTALL_DIR}/scripts/lib/python311.sh"

# ── 4. Python sanal ortamı ───────────────────────────────────────────────────
echo "[4/8] Python sanal ortamı oluşturuluyor..."
"${PYTHON}" -m venv "${INSTALL_DIR}/venv"
# shellcheck disable=SC1091
source "${INSTALL_DIR}/venv/bin/activate"

if [ ! -d "${PAYLOAD_DIR}/wheelhouse" ]; then
    echo "HATA: payload/wheelhouse eksik."
    exit 1
fi

pip install --no-index --find-links="${PAYLOAD_DIR}/wheelhouse" \
    pip wheel setuptools --quiet
pip install --no-index --find-links="${PAYLOAD_DIR}/wheelhouse" \
    -r "${PAYLOAD_DIR}/worker/requirements.txt" --quiet
echo "  ✓ Python bağımlılıkları yüklendi (çevrimdışı)"

# ── 5. İşçi uygulaması ───────────────────────────────────────────────────────
echo "[5/8] İşçi dosyaları kopyalanıyor..."
rsync -a \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='packaging/bin' \
    --exclude='packaging/wheelhouse' \
    "${PAYLOAD_DIR}/worker/" "${INSTALL_DIR}/worker/"

chmod +x "${INSTALL_DIR}/worker/worker.sh"

# ── 6. Whisper modeli (sürümlü, güncelleme-güvenli) ───────────────────────────
echo "[6/8] Whisper Medium modeli kuruluyor..."
MODEL_SRC="${PAYLOAD_DIR}/models/whisper-medium-mlx"
export INSTALL_DIR MODEL_ROOT="${MODEL_DIR}" MODEL_SRC MODEL_ID=whisper-medium-mlx
bash "${PAYLOAD_DIR}/scripts/install-model.sh"
MODEL_DEST="${MODEL_DIR}/current"

# ── 7. Yapılandırma ──────────────────────────────────────────────────────────
echo "[7/8] Yapılandırma oluşturuluyor..."
COORD_HOST_LINE=""
COORD_HOST="$(_resolve_coordinator_host || true)"
if [ -n "${COORD_HOST}" ]; then
    COORD_HOST_LINE="COORDINATOR_HOST=${COORD_HOST}"
else
    COORD_HOST_LINE="# COORDINATOR_HOST=  # paket içi coordinator-host.txt yok — mDNS veya elle ayarlayın"
fi
if [ ! -f "${INSTALL_DIR}/worker/.env" ]; then
    cat > "${INSTALL_DIR}/worker/.env" << EOF
# Koordinatör
${COORD_HOST_LINE}
COORDINATOR_PORT=8080

MODEL_PATH=${MODEL_DIR}/current
WHISPER_LANGUAGE=tr
WHISPER_WORD_TIMESTAMPS=true
TEMP_DIR=/tmp/transcription-jobs
LOG_LEVEL=INFO
JSON_LOGS=true
HEARTBEAT_INTERVAL_SECONDS=30
JOB_POLL_INTERVAL_SECONDS=5
EOF
    echo "  ✓ ${INSTALL_DIR}/worker/.env oluşturuldu"
else
    echo "  ℹ .env zaten mevcut, korunuyor"
    if [ -n "${COORD_HOST}" ]; then
        _apply_coordinator_host "${COORD_HOST}"
    fi
fi

# ── 8. launchd servisi ───────────────────────────────────────────────────────
echo "[8/8] Sistem servisi kuruluyor..."
PLIST_SRC="${PAYLOAD_DIR}/launchd/com.transcription.worker.plist"
if [ ! -f "${PLIST_SRC}" ]; then
    echo "HATA: launchd plist eksik: ${PLIST_SRC}"
    exit 1
fi

sed \
    -e "s|INSTALL_DIR|${INSTALL_DIR}|g" \
    -e "s|LOG_DIR|${LOG_DIR}|g" \
    "${PLIST_SRC}" | sudo tee "${LAUNCHD_PLIST}" > /dev/null

sudo launchctl bootout system "${LAUNCHD_PLIST}" 2>/dev/null || true
sudo launchctl bootstrap system "${LAUNCHD_PLIST}"

HOSTNAME="$(hostname)"
echo ""
echo "=== İşçi Kurulumu Tamamlandı! ==="
echo ""
echo "Makine adı    : ${HOSTNAME}"
echo "Kurulum dizini: ${INSTALL_DIR}"
echo "Model         : ${MODEL_DEST}"
echo "ffmpeg        : ${INSTALL_DIR}/bin/ffmpeg"
echo ""
echo "Servis durumu : sudo launchctl list com.transcription.worker"
echo "Loglar        : tail -f ${LOG_DIR}/worker.log"
echo ""
echo "Manuel başlatma: ${INSTALL_DIR}/worker/worker.sh"
echo "Koordinatör IP için: nano ${INSTALL_DIR}/worker/.env"
