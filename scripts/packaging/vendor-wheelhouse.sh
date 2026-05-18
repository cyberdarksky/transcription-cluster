#!/usr/bin/env bash
# Worker wheelhouse'u worker/packaging/wheelhouse/ altına indirir (bir kez, internet).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/lib/python311.sh"
VENDOR_DIR="${REPO_ROOT}/worker/packaging/wheelhouse"

resolve_build_python311

if [ "$(uname -m)" != "arm64" ]; then
    echo "HATA: Wheelhouse arm64 Mac üzerinde indirilmelidir."
    exit 1
fi

mkdir -p "${VENDOR_DIR}"

echo "Wheelhouse indiriliyor -> ${VENDOR_DIR}"
echo "  (hedef platform = bu makine: macOS arm64 + Python 3.11)"
BUILD_VENV="$(mktemp -d "${TMPDIR:-/tmp}/vendor-wheelhouse-venv.XXXXXX")"
"${PYTHON}" -m venv "${BUILD_VENV}"
# shellcheck disable=SC1091
source "${BUILD_VENV}/bin/activate"
pip install -q pip wheel

pip download \
    --dest "${VENDOR_DIR}" \
    pip wheel setuptools \
    -r "${REPO_ROOT}/worker/requirements.txt"

deactivate
rm -rf "${BUILD_VENV}"

WHEEL_COUNT="$(find "${VENDOR_DIR}" -name '*.whl' | wc -l | tr -d ' ')"
if [ "${WHEEL_COUNT}" -lt 5 ]; then
    echo "HATA: wheelhouse çok az dosya içeriyor (${WHEEL_COUNT})."
    exit 1
fi
echo "  ✓ ${WHEEL_COUNT} wheel dosyası"
