#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# İşçi kurulum paketini oluşturur (Apple Silicon, çevrimdışı kurulum).
#
# Kullanım:
#   ./scripts/build_worker_package.sh              # Tam paket (model dahil)
#   ./scripts/build_worker_package.sh --skip-model # Model olmadan (kod güncellemesi)
#
# Gereksinimler (bu betik INTERNET kullanır — hedef makinelerde değil):
#   - macOS arm64
#   - Python 3.11+
#   - curl, unzip, rsync
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE_DIR="${REPO_ROOT}/dist/worker-package"
VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
SKIP_MODEL=false
FETCH_FFMPEG=true

for arg in "$@"; do
    case "${arg}" in
        --skip-model) SKIP_MODEL=true ;;
        --no-ffmpeg-fetch) FETCH_FFMPEG=false ;;
        -h|--help)
            echo "Kullanım: $0 [--skip-model] [--no-ffmpeg-fetch]"
            exit 0
            ;;
        *)
            echo "Bilinmeyen argüman: ${arg}"
            exit 1
            ;;
    esac
done

if [ "$(uname -m)" != "arm64" ]; then
    echo "HATA: Paket arm64 için derlenmelidir. Mevcut: $(uname -m)"
    exit 1
fi

# Hedef kurulum: Python 3.12 (mlx wheel uyumluluğu)
PYTHON=""
for candidate in python3.12 python3.11; do
    if command -v "${candidate}" &>/dev/null; then
        PYTHON="${candidate}"
        break
    fi
done
if [ -z "${PYTHON}" ]; then
    echo "HATA: Paket oluşturmak için python3.12 veya python3.11 gerekli."
    echo "  python.org veya pyenv ile Python 3.12 kurun."
    exit 1
fi
TARGET_PY_VER="3.12"

echo "=== İşçi Paketi Hazırlanıyor (v${VERSION}) ==="
echo "Python: $("${PYTHON}" --version)"
echo ""

rm -rf "${PACKAGE_DIR}"
mkdir -p "${PACKAGE_DIR}/payload"/{wheelhouse,models,bin,launchd,python}

# ── 1. ffmpeg / ffprobe ───────────────────────────────────────────────────────
echo "[1/6] ffmpeg araçları hazırlanıyor..."
FFMPEG_SRC="${REPO_ROOT}/worker/packaging/bin"
if [ -x "${FFMPEG_SRC}/ffmpeg" ] && [ -x "${FFMPEG_SRC}/ffprobe" ]; then
    cp "${FFMPEG_SRC}/ffmpeg" "${FFMPEG_SRC}/ffprobe" "${PACKAGE_DIR}/payload/bin/"
    [ -f "${FFMPEG_SRC}/VERSION" ] && cp "${FFMPEG_SRC}/VERSION" "${PACKAGE_DIR}/payload/bin/"
elif [ "${FETCH_FFMPEG}" = true ]; then
    bash "${REPO_ROOT}/scripts/packaging/fetch-ffmpeg.sh" "${PACKAGE_DIR}/payload/bin"
    mkdir -p "${FFMPEG_SRC}"
    cp "${PACKAGE_DIR}/payload/bin/ffmpeg" "${PACKAGE_DIR}/payload/bin/ffprobe" "${FFMPEG_SRC}/" 2>/dev/null || true
else
    echo "HATA: ffmpeg bulunamadı. fetch-ffmpeg.sh çalıştırın veya --no-ffmpeg-fetch kullanmayın."
    exit 1
fi
echo "  ✓ ffmpeg pakete eklendi"

# ── 2. Whisper modeli ─────────────────────────────────────────────────────────
echo "[2/6] Whisper Medium MLX modeli..."
MODEL_DEST="${PACKAGE_DIR}/payload/models/whisper-medium-mlx"
mkdir -p "${PACKAGE_DIR}/payload/models"

if [ "${SKIP_MODEL}" = true ]; then
    echo "  ℹ --skip-model: model atlanıyor (mevcut kurulumda model korunur)"
    mkdir -p "${MODEL_DEST}"
    echo "PLACEHOLDER — kurulumda mevcut model kullanılacak" > "${MODEL_DEST}/.skip"
else
  if [ -d "${REPO_ROOT}/worker/packaging/models/whisper-medium-mlx" ] \
      && [ -f "${REPO_ROOT}/worker/packaging/models/whisper-medium-mlx/model.safetensors" ]; then
      echo "  Yerel model paketleniyor..."
      rsync -a "${REPO_ROOT}/worker/packaging/models/whisper-medium-mlx/" "${MODEL_DEST}/"
  else
      bash "${REPO_ROOT}/scripts/packaging/bundle-model.sh"
      rsync -a "${REPO_ROOT}/worker/packaging/models/whisper-medium-mlx/" "${MODEL_DEST}/"
  fi
  if [ ! -f "${MODEL_DEST}/MANIFEST.json" ]; then
      python3 "${REPO_ROOT}/scripts/packaging/write_model_manifest.py" \
          --bundle-dir "${MODEL_DEST}" \
          --model-id whisper-medium-mlx \
          --version "${MODEL_VERSION:-1.0.0}"
  fi
  python3 "${REPO_ROOT}/scripts/packaging/write_model_manifest.py" \
      --bundle-dir "${MODEL_DEST}" \
      --model-id whisper-medium-mlx \
      --version "${MODEL_VERSION:-1.0.0}" \
      --verify-only
  MODEL_SIZE="$(du -sh "${MODEL_DEST}" | cut -f1)"
  echo "  ✓ Model boyutu: ${MODEL_SIZE}"
fi

# ── 3. Python wheelhouse ──────────────────────────────────────────────────────
echo "[3/6] Python wheel'ları indiriliyor..."
BUILD_VENV="$(mktemp -d "${TMPDIR:-/tmp}/worker-build-venv.XXXXXX")"
"${PYTHON}" -m venv "${BUILD_VENV}"
# shellcheck disable=SC1091
source "${BUILD_VENV}/bin/activate"
pip install -q pip wheel

if ! pip download \
    --platform macosx_14_0_arm64 \
    --python-version "${TARGET_PY_VER}" \
    --only-binary=:all: \
    --dest "${PACKAGE_DIR}/payload/wheelhouse" \
    -r "${REPO_ROOT}/worker/requirements.txt"; then
    echo "  ℹ Sadece-binary indirme başarısız; bağımlılık çözümü ile deneniyor..."
    pip download \
        --platform macosx_14_0_arm64 \
        --python-version "${TARGET_PY_VER}" \
        --dest "${PACKAGE_DIR}/payload/wheelhouse" \
        -r "${REPO_ROOT}/worker/requirements.txt"
fi
deactivate
rm -rf "${BUILD_VENV}"

WHEEL_COUNT="$(find "${PACKAGE_DIR}/payload/wheelhouse" -name '*.whl' | wc -l | tr -d ' ')"
echo "  ✓ ${WHEEL_COUNT} wheel dosyası"

# ── 4. İşçi kaynak kodu ───────────────────────────────────────────────────────
echo "[4/6] İşçi kodu kopyalanıyor..."
rsync -a \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='packaging/models' \
    --exclude='packaging/bin/ffmpeg' \
    --exclude='packaging/bin/ffprobe' \
    "${REPO_ROOT}/worker/" "${PACKAGE_DIR}/payload/worker/"

chmod +x "${PACKAGE_DIR}/payload/worker/worker.sh"

# ── 5. Kurulum dosyaları ──────────────────────────────────────────────────────
echo "[5/6] Kurulum dosyaları ekleniyor..."
cp "${REPO_ROOT}/scripts/install/worker/install-worker.sh" "${PACKAGE_DIR}/"
cp "${REPO_ROOT}/scripts/install/worker/uninstall.sh" "${PACKAGE_DIR}/"
chmod +x "${PACKAGE_DIR}/install-worker.sh" "${PACKAGE_DIR}/uninstall.sh"

mkdir -p "${PACKAGE_DIR}/payload/scripts"
cp "${REPO_ROOT}/scripts/packaging/install-model.sh" \
    "${PACKAGE_DIR}/payload/scripts/"
chmod +x "${PACKAGE_DIR}/payload/scripts/install-model.sh"

cp "${REPO_ROOT}/scripts/launchd/worker/com.transcription.worker.plist" \
    "${PACKAGE_DIR}/payload/launchd/"

cp "${REPO_ROOT}/scripts/packaging/verify-worker-install.sh" "${PACKAGE_DIR}/"
chmod +x "${PACKAGE_DIR}/verify-worker-install.sh"

# Opsiyonel: Python .pkg (varsa kopyala)
if compgen -G "${REPO_ROOT}/worker/packaging/python/python-3.12*.pkg" > /dev/null; then
    cp "${REPO_ROOT}"/worker/packaging/python/python-3.12*.pkg "${PACKAGE_DIR}/payload/python/"
    echo "  ✓ Python .pkg pakete eklendi"
fi

cat > "${PACKAGE_DIR}/MANIFEST.txt" << EOF
Transkripsiyon İşçi Paketi v${VERSION}
Platform: macOS 14+ arm64
Oluşturulma: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

İçerik:
  install-worker.sh     — Kurulum betiği
  uninstall.sh          — Kaldırma betiği
  verify-worker-install.sh — Kurulum doğrulama
  payload/worker/       — İşçi uygulaması
  payload/wheelhouse/   — Python bağımlılıkları (çevrimdışı)
  payload/models/       — Whisper Medium MLX modeli
  payload/bin/          — ffmpeg + ffprobe
  payload/launchd/      — Sistem servisi tanımı
EOF

# ── 6. Arşiv ──────────────────────────────────────────────────────────────────
echo "[6/6] Arşiv oluşturuluyor..."
mkdir -p "${REPO_ROOT}/dist"
ARCHIVE="${REPO_ROOT}/dist/worker-v${VERSION}-arm64.tar.gz"
tar -czf "${ARCHIVE}" -C "${REPO_ROOT}/dist" worker-package/

echo ""
echo "=== Paket Hazır ==="
echo "Dizin  : ${PACKAGE_DIR}"
echo "Arşiv  : ${ARCHIVE}"
echo "Boyut  : $(du -sh "${ARCHIVE}" | cut -f1)"
echo ""
echo "USB'ye kopyala:"
echo "  cp ${ARCHIVE} /Volumes/USB/"
echo ""
echo "Hedef makinede:"
echo "  tar -xzf worker-v${VERSION}-arm64.tar.gz"
echo "  cd worker-package && ./install-worker.sh"
