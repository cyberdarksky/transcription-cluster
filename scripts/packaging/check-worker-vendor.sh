#!/usr/bin/env bash
# worker/packaging/ içinde çevrimdışı build için gerekli dosyaları doğrular.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENDOR="${REPO_ROOT}/worker/packaging"
SKIP_MODEL=false
REQUIRE_PYTHON_PKG=true

for arg in "$@"; do
    case "${arg}" in
        --skip-model) SKIP_MODEL=true ;;
        --no-python-pkg) REQUIRE_PYTHON_PKG=false ;;
    esac
done

missing=()
warn=()

if ! find "${VENDOR}/wheelhouse" -name '*.whl' 2>/dev/null | grep -q .; then
    missing+=("wheelhouse/ (*.whl) — ./scripts/packaging/vendor-wheelhouse.sh")
fi

if [ ! -x "${VENDOR}/bin/ffmpeg" ] || [ ! -x "${VENDOR}/bin/ffprobe" ]; then
    missing+=("bin/ffmpeg + bin/ffprobe — ./scripts/packaging/fetch-ffmpeg.sh")
fi

if [ "${SKIP_MODEL}" = false ]; then
    if [ ! -f "${VENDOR}/models/whisper-medium-mlx/weights.npz" ]; then
        missing+=("models/whisper-medium-mlx/ — ./scripts/packaging/bundle-model.sh")
    fi
fi

if [ "${REQUIRE_PYTHON_PKG}" = true ]; then
    if ! compgen -G "${VENDOR}/python/python-3.11*.pkg" > /dev/null; then
        warn+=("python/python-3.11*.pkg — hedef Mac'te python3.11 yoksa gerekli")
        warn+=("  ./scripts/packaging/fetch-python311-pkg.sh")
    fi
fi

if [ "${#missing[@]}" -gt 0 ]; then
    echo "HATA: worker/packaging/ eksik:" >&2
    for line in "${missing[@]}"; do
        echo "  • ${line}" >&2
    done
    echo "" >&2
    echo "Hepsini bir seferde indirmek için:" >&2
    echo "  ./scripts/packaging/vendor-worker-bundle.sh" >&2
    exit 1
fi

if [ "${#warn[@]}" -gt 0 ]; then
    echo "UYARI:" >&2
    for line in "${warn[@]}"; do
        echo "  • ${line}" >&2
    done
fi

echo "OK: worker/packaging/ çevrimdışı build için hazır"
