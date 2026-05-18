#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# İşçi kurulum paketini oluşturur (Apple Silicon, çevrimdışı kurulum).
#
# Varsayılan: ÇEVRİMDIŞI — sadece worker/packaging/ içindekileri kopyalar.
# Internet kullanmaz; brew/python kurulumu istemez.
#
# Kullanım:
#   ./scripts/build_worker_package.sh              # vendor hazırsa, internet yok
#   ./scripts/build_worker_package.sh --fetch-missing  # eksikleri indir, sonra paketle
#   ./scripts/build_worker_package.sh --skip-model
#
# İlk kez (bir makinede, internet — bir kez):
#   ./scripts/packaging/vendor-worker-bundle.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="${REPO_ROOT}/worker/packaging"
PACKAGE_DIR="${REPO_ROOT}/dist/worker-package"
VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
SKIP_MODEL=false
FETCH_MISSING=false

for arg in "$@"; do
    case "${arg}" in
        --skip-model) SKIP_MODEL=true ;;
        --fetch-missing) FETCH_MISSING=true ;;
        -h|--help)
            cat <<'EOF'
Kullanım: build_worker_package.sh [--skip-model] [--fetch-missing]

Çevrimdışı paket için önce (bir kez, internet):
  ./scripts/packaging/vendor-worker-bundle.sh

Sonra her zaman (internet gerekmez):
  ./scripts/build_worker_package.sh

Eksik dosya varsa otomatik indirmek için:
  ./scripts/build_worker_package.sh --fetch-missing
EOF
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

if [ "${FETCH_MISSING}" = true ]; then
    echo "Eksik vendor dosyaları indiriliyor..."
    ARGS=()
    [ "${SKIP_MODEL}" = true ] && ARGS+=(--skip-model)
    bash "${REPO_ROOT}/scripts/packaging/vendor-worker-bundle.sh" "${ARGS[@]}"
fi

if [ "${SKIP_MODEL}" = true ]; then
    bash "${REPO_ROOT}/scripts/packaging/check-worker-vendor.sh" --skip-model
else
    bash "${REPO_ROOT}/scripts/packaging/check-worker-vendor.sh"
fi

echo "=== İşçi Paketi Hazırlanıyor (v${VERSION}, çevrimdışı) ==="
echo ""

rm -rf "${PACKAGE_DIR}"
mkdir -p "${PACKAGE_DIR}/payload"/{wheelhouse,models,bin,launchd,python}

# ── 1. ffmpeg ─────────────────────────────────────────────────────────────────
echo "[1/5] ffmpeg..."
cp "${VENDOR}/bin/ffmpeg" "${VENDOR}/bin/ffprobe" "${PACKAGE_DIR}/payload/bin/"
[ -f "${VENDOR}/bin/VERSION" ] && cp "${VENDOR}/bin/VERSION" "${PACKAGE_DIR}/payload/bin/"
echo "  ✓ ffmpeg pakete eklendi"

# ── 2. Whisper modeli ─────────────────────────────────────────────────────────
echo "[2/5] Whisper modeli..."
MODEL_DEST="${PACKAGE_DIR}/payload/models/whisper-medium-mlx"
if [ "${SKIP_MODEL}" = true ]; then
    mkdir -p "${MODEL_DEST}"
    echo "PLACEHOLDER — kurulumda mevcut model kullanılacak" > "${MODEL_DEST}/.skip"
    echo "  ℹ --skip-model"
else
    rsync -a \
        --exclude='.cache' \
        "${VENDOR}/models/whisper-medium-mlx/" "${MODEL_DEST}/"
    python3 "${REPO_ROOT}/scripts/packaging/write_model_manifest.py" \
        --bundle-dir "${MODEL_DEST}" \
        --model-id whisper-medium-mlx \
        --version "${MODEL_VERSION:-1.0.0}" \
        --verify-only
    echo "  ✓ Model: $(du -sh "${MODEL_DEST}" | cut -f1)"
fi

# ── 3. Wheelhouse ─────────────────────────────────────────────────────────────
echo "[3/5] Python wheelhouse..."
rsync -a "${VENDOR}/wheelhouse/" "${PACKAGE_DIR}/payload/wheelhouse/"
WHEEL_COUNT="$(find "${PACKAGE_DIR}/payload/wheelhouse" -name '*.whl' | wc -l | tr -d ' ')"
echo "  ✓ ${WHEEL_COUNT} wheel (kopyalandı, indirme yok)"

# ── 4. İşçi kaynak kodu ───────────────────────────────────────────────────────
echo "[4/5] İşçi kodu..."
rsync -a \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='packaging/models' \
    --exclude='packaging/wheelhouse' \
    --exclude='packaging/python' \
    --exclude='packaging/bin/ffmpeg' \
    --exclude='packaging/bin/ffprobe' \
    "${REPO_ROOT}/worker/" "${PACKAGE_DIR}/payload/worker/"
chmod +x "${PACKAGE_DIR}/payload/worker/worker.sh"

# ── 5. Kurulum dosyaları + arşiv ──────────────────────────────────────────────
echo "[5/5] Kurulum dosyaları ve arşiv..."
cp "${REPO_ROOT}/scripts/install/worker/install-worker.sh" "${PACKAGE_DIR}/"
cp "${REPO_ROOT}/scripts/install/worker/uninstall.sh" "${PACKAGE_DIR}/"
chmod +x "${PACKAGE_DIR}/install-worker.sh" "${PACKAGE_DIR}/uninstall.sh"

mkdir -p "${PACKAGE_DIR}/payload/scripts/lib"
cp "${REPO_ROOT}/scripts/packaging/install-model.sh" \
    "${PACKAGE_DIR}/payload/scripts/"
cp "${REPO_ROOT}/scripts/lib/python311.sh" \
    "${PACKAGE_DIR}/payload/scripts/lib/"
chmod +x "${PACKAGE_DIR}/payload/scripts/install-model.sh"
chmod +x "${PACKAGE_DIR}/payload/scripts/lib/python311.sh"

cp "${REPO_ROOT}/scripts/launchd/worker/com.transcription.worker.plist" \
    "${PACKAGE_DIR}/payload/launchd/"

cp "${REPO_ROOT}/scripts/packaging/verify-worker-install.sh" "${PACKAGE_DIR}/"
chmod +x "${PACKAGE_DIR}/verify-worker-install.sh"

# Koordinatör IP (bu Mac'in LAN adresi) — işçi .env için
MASTER_LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
if [ -n "${MASTER_LAN_IP}" ]; then
    echo "${MASTER_LAN_IP}" > "${PACKAGE_DIR}/coordinator-host.txt"
    echo "  ✓ coordinator-host.txt → ${MASTER_LAN_IP}"
fi

if compgen -G "${VENDOR}/python/python-3.11*.pkg" > /dev/null; then
    cp "${VENDOR}"/python/python-3.11*.pkg "${PACKAGE_DIR}/payload/python/"
    echo "  ✓ Python 3.11 .pkg pakete eklendi"
else
    echo "  UYARI: python-3.11*.pkg yok — hedef Mac'te python3.11 önceden kurulu olmalı"
fi

cat > "${PACKAGE_DIR}/MANIFEST.txt" << EOF
Transkripsiyon İşçi Paketi v${VERSION}
Platform: macOS 14+ arm64
Oluşturulma: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Mod: çevrimdışı (vendor kopyası)

İçerik:
  install-worker.sh     — Kurulum betiği
  uninstall.sh          — Kaldırma betiği
  verify-worker-install.sh — Kurulum doğrulama
  payload/worker/       — İşçi uygulaması
  payload/wheelhouse/   — Python bağımlılıkları
  payload/models/       — Whisper Medium MLX modeli
  payload/bin/          — ffmpeg + ffprobe
  payload/python/       — Python 3.11 kurulum paketi (varsa)
  payload/launchd/      — Sistem servisi tanımı
EOF

mkdir -p "${REPO_ROOT}/dist"
ARCHIVE="${REPO_ROOT}/dist/worker-v${VERSION}-arm64.tar.gz"
tar -czf "${ARCHIVE}" -C "${REPO_ROOT}/dist" worker-package/

echo ""
echo "=== Paket Hazır (internet kullanılmadı) ==="
echo "Dizin  : ${PACKAGE_DIR}"
echo "Arşiv  : ${ARCHIVE}"
echo "Boyut  : $(du -sh "${ARCHIVE}" | cut -f1)"
echo ""
echo "Hedef makinede:"
echo "  tar -xzf worker-v${VERSION}-arm64.tar.gz"
echo "  cd worker-package && ./install-worker.sh"
