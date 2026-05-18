#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# macOS arm64 için ffmpeg ve ffprobe indirir (paket hazırlama aşaması).
#
# Kullanım:
#   ./scripts/packaging/fetch-ffmpeg.sh [hedef_dizin]
#
# Varsayılan hedef: worker/packaging/bin
# Çıktı: ffmpeg, ffprobe (çalıştırılabilir)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST_DIR="${1:-${REPO_ROOT}/worker/packaging/bin}"
FFMPEG_VERSION="${FFMPEG_VERSION:-7.1}"

mkdir -p "${DEST_DIR}"

if [ "$(uname -m)" != "arm64" ]; then
    echo "UYARI: ffmpeg arm64 derlemesi indiriliyor; bu makine: $(uname -m)"
fi

download_binary() {
    local name="$1"
    local url="$2"
    local tmp_zip
    tmp_zip="$(mktemp -t "${name}.XXXXXX.zip")"

    echo "  İndiriliyor: ${name} (${FFMPEG_VERSION})..."
    curl -fsSL --retry 3 --retry-delay 2 -o "${tmp_zip}" "${url}"
    unzip -oq -j "${tmp_zip}" "${name}" -d "${DEST_DIR}"
    rm -f "${tmp_zip}"
    chmod +x "${DEST_DIR}/${name}"
}

# evermeet.cx — macOS için yaygın statik arm64 derlemeleri
download_binary "ffmpeg" "https://evermeet.cx/ffmpeg/ffmpeg-${FFMPEG_VERSION}.zip"
download_binary "ffprobe" "https://evermeet.cx/ffmpeg/ffprobe-${FFMPEG_VERSION}.zip"

# Doğrulama
"${DEST_DIR}/ffmpeg" -version | head -n 1
"${DEST_DIR}/ffprobe" -version | head -n 1

cat > "${DEST_DIR}/VERSION" << EOF
ffmpeg=${FFMPEG_VERSION}
arch=arm64
source=evermeet.cx
EOF

echo ""
echo "ffmpeg hazır: ${DEST_DIR}/ffmpeg"
echo "ffprobe hazır: ${DEST_DIR}/ffprobe"
