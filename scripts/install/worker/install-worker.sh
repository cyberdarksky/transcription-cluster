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

# Python 3.12 tercih edilir; paket içi .pkg varsa kurulur
PYTHON=""
for candidate in python3.12 python3.11 python3; do
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
        echo "  payload/python/ altına python-3.12.x-macos14-arm64.pkg ekleyin veya python.org'dan kurun."
        exit 1
    fi
fi

echo "  ✓ macOS $(sw_vers -productVersion) (${ARCH})"
echo "  ✓ Python $("${PYTHON}" --version 2>&1)"

# ── 2. Dizin yapısı ──────────────────────────────────────────────────────────
echo "[2/8] Dizin yapısı oluşturuluyor..."
sudo mkdir -p "${INSTALL_DIR}"/{worker,venv,bin}
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

# ── 4. Python sanal ortamı ───────────────────────────────────────────────────
echo "[4/8] Python sanal ortamı oluşturuluyor..."
"${PYTHON}" -m venv "${INSTALL_DIR}/venv"
# shellcheck disable=SC1091
source "${INSTALL_DIR}/venv/bin/activate"

if [ ! -d "${PAYLOAD_DIR}/wheelhouse" ]; then
    echo "HATA: payload/wheelhouse eksik."
    exit 1
fi

pip install --upgrade pip wheel setuptools --quiet
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

# ── 6. Whisper modeli ────────────────────────────────────────────────────────
echo "[6/8] Whisper Medium modeli kopyalanıyor..."
MODEL_DEST="${MODEL_DIR}/whisper-medium-mlx"
MODEL_SRC="${PAYLOAD_DIR}/models/whisper-medium-mlx"

if [ -f "${MODEL_SRC}/.skip" ]; then
    if [ ! -d "${MODEL_DEST}" ]; then
        echo "HATA: Bu paket model içermiyor ve hedefte model yok."
        echo "  Önce tam paketi kurun veya modeli ${MODEL_DEST} altına kopyalayın."
        exit 1
    fi
    echo "  ℹ Model paketi atlandı — mevcut kurulum kullanılıyor"
elif [ ! -d "${MODEL_SRC}" ] || [ ! -f "${MODEL_SRC}/model.safetensors" ]; then
    echo "HATA: Model dizini eksik veya eksik dosyalar: ${MODEL_SRC}"
    echo "  Paketi ./scripts/build_worker_package.sh ile oluşturun."
    exit 1
elif [ -d "${MODEL_DEST}" ]; then
    echo "  ℹ Model zaten mevcut, atlanıyor: ${MODEL_DEST}"
else
    echo "  Model kopyalanıyor (~3 GB, birkaç dakika sürebilir)..."
    cp -R "${MODEL_SRC}" "${MODEL_DEST}"
    echo "  ✓ Model kopyalandı"
fi

for required in config.json model.safetensors tokenizer.json; do
    if [ ! -f "${MODEL_DEST}/${required}" ]; then
        echo "HATA: Eksik model dosyası: ${required}"
        exit 1
    fi
done

HASH_FILE="${PAYLOAD_DIR}/models/whisper-medium-mlx.sha256"
if [ -f "${HASH_FILE}" ]; then
    EXPECTED_HASH="$(tr -d '[:space:]' < "${HASH_FILE}")"
    ACTUAL_HASH="$(find "${MODEL_DEST}" -name '*.safetensors' | sort | xargs md5 -q | md5 -q)"
    if [ "${EXPECTED_HASH}" != "${ACTUAL_HASH}" ]; then
        echo "UYARI: Model karması uyuşmuyor — dosyalar bozulmuş olabilir."
    else
        echo "  ✓ Model bütünlük doğrulaması geçti"
    fi
fi

# ── 7. Yapılandırma ──────────────────────────────────────────────────────────
echo "[7/8] Yapılandırma oluşturuluyor..."
if [ ! -f "${INSTALL_DIR}/worker/.env" ]; then
    cat > "${INSTALL_DIR}/worker/.env" << EOF
# Koordinatör — boş bırakılırsa mDNS otomatik keşif kullanılır
# COORDINATOR_HOST=192.168.1.101
COORDINATOR_PORT=8080

MODEL_PATH=${MODEL_DEST}
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
