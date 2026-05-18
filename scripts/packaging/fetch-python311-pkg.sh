#!/usr/bin/env bash
# Python 3.11 macOS universal2 .pkg indirir -> worker/packaging/python/
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST_DIR="${REPO_ROOT}/worker/packaging/python"
PYTHON_VERSION="${PYTHON_PKG_VERSION:-3.11.9}"
PKG_NAME="python-${PYTHON_VERSION}-macos11.pkg"
URL="https://www.python.org/ftp/python/${PYTHON_VERSION}/${PKG_NAME}"

mkdir -p "${DEST_DIR}"
DEST="${DEST_DIR}/${PKG_NAME}"

if [ -f "${DEST}" ]; then
    echo "Python .pkg zaten mevcut: ${DEST}"
    exit 0
fi

echo "Python ${PYTHON_VERSION} .pkg indiriliyor..."
curl -fsSL --retry 3 --retry-delay 2 -o "${DEST}" "${URL}"
echo "  ✓ ${DEST} ($(du -sh "${DEST}" | cut -f1))"
