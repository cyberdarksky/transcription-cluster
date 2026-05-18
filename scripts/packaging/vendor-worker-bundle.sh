#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# İşçi paketinin TÜM büyük bileşenlerini worker/packaging/ altına indirir.
# Bunu bir kez (internet olan makinede) çalıştırın; sonra build çevrimdışıdır.
#
#   ./scripts/packaging/vendor-worker-bundle.sh
#   ./scripts/packaging/vendor-worker-bundle.sh --skip-model
#   ./scripts/packaging/vendor-worker-bundle.sh --skip-python-pkg
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SKIP_MODEL=false
SKIP_PYTHON_PKG=false

for arg in "$@"; do
    case "${arg}" in
        --skip-model) SKIP_MODEL=true ;;
        --skip-python-pkg) SKIP_PYTHON_PKG=true ;;
        -h|--help)
            cat <<'EOF'
Kullanım: vendor-worker-bundle.sh [--skip-model] [--skip-python-pkg]

worker/packaging/ altına yazar:
  wheelhouse/     — mlx, mlx-whisper, tüm pip bağımlılıkları
  bin/            — ffmpeg + ffprobe
  models/         — whisper-medium-mlx (~1.5 GB)
  python/         — python-3.11.x-macos11.pkg

Sonra (internet gerekmez):
  ./scripts/build_worker_package.sh
EOF
            exit 0
            ;;
        *)
            echo "Bilinmeyen argüman: ${arg}"
            exit 1
            ;;
    esac
done

echo "=== İşçi vendor bundle (internet) ==="
echo ""

echo "[1/4] Wheelhouse..."
bash "${REPO_ROOT}/scripts/packaging/vendor-wheelhouse.sh"

echo ""
echo "[2/4] ffmpeg..."
bash "${REPO_ROOT}/scripts/packaging/fetch-ffmpeg.sh" "${REPO_ROOT}/worker/packaging/bin"

if [ "${SKIP_MODEL}" = false ]; then
    echo ""
    echo "[3/4] Whisper modeli..."
    bash "${REPO_ROOT}/scripts/packaging/bundle-model.sh"
else
    echo ""
    echo "[3/4] Model atlandı (--skip-model)"
fi

if [ "${SKIP_PYTHON_PKG}" = false ]; then
    echo ""
    echo "[4/4] Python 3.11 .pkg..."
    bash "${REPO_ROOT}/scripts/packaging/fetch-python311-pkg.sh"
else
    echo ""
    echo "[4/4] Python .pkg atlandı (--skip-python-pkg)"
fi

echo ""
echo "=== Vendor tamam ==="
echo "Çevrimdışı paket oluşturmak için:"
echo "  ./scripts/build_worker_package.sh"
