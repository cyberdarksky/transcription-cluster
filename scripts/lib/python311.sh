#!/usr/bin/env bash
# Shared Python 3.11 resolver for build, install, and dev scripts.
# MLX / mlx-whisper wheels target CPython 3.11 on macOS arm64.
#
# Usage:
#   source "$(dirname "$0")/../lib/python311.sh"   # adjust path
#   require_python311 "${PAYLOAD_DIR}/python"      # install scripts
#   resolve_build_python311                        # build scripts (sets PYTHON)
#
# Sets: PYTHON, TARGET_PY_VER

set -euo pipefail

TARGET_PY_VER="3.11"

_python311_version_ok() {
    local py="$1"
    "${py}" -c 'import sys; exit(0 if sys.version_info[:2] == (3, 11) else 1)' 2>/dev/null
}

_python_version_label() {
    local py="$1"
    "${py}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null \
        || echo "unknown"
}

# Install / runtime: require exactly Python 3.11.x
require_python311() {
    local payload_python_dir="${1:-}"
    PYTHON=""

    if command -v python3.11 &>/dev/null && _python311_version_ok python3.11; then
        PYTHON="python3.11"
        return 0
    fi

    if [ -n "${payload_python_dir}" ] && [ -d "${payload_python_dir}" ]; then
        local pkg_311 pkg_312
        pkg_311="$(find "${payload_python_dir}" -maxdepth 1 -name 'python-3.11*.pkg' 2>/dev/null | head -n 1 || true)"
        pkg_312="$(find "${payload_python_dir}" -maxdepth 1 -name 'python-3.12*.pkg' 2>/dev/null | head -n 1 || true)"

        if [ -n "${pkg_312}" ] && [ -z "${pkg_311}" ]; then
            echo "HATA: Pakette Python 3.12 .pkg var; bu proje Python 3.11 hedefliyor." >&2
            echo "  payload/python/ altına python-3.11.x-macos11.pkg (arm64) ekleyin." >&2
            exit 1
        fi

        if [ -n "${pkg_311}" ] && [ -f "${pkg_311}" ]; then
            echo "  Python 3.11 paket içinden kuruluyor..."
            sudo installer -pkg "${pkg_311}" -target /
            if command -v python3.11 &>/dev/null && _python311_version_ok python3.11; then
                PYTHON="python3.11"
                return 0
            fi
            echo "HATA: Python 3.11 kurulumu tamamlandı ancak python3.11 bulunamadı." >&2
            exit 1
        fi
    fi

    if command -v python3 &>/dev/null; then
        local ver
        ver="$(_python_version_label python3)"
        echo "HATA: Python 3.11 gerekli; mevcut: ${ver}" >&2
        echo "  brew install python@3.11" >&2
        echo "  veya https://www.python.org/downloads/release/python-3110/" >&2
        exit 1
    fi

    echo "HATA: python3.11 bulunamadı." >&2
    echo "  brew install python@3.11 veya pakete python-3.11*.pkg ekleyin." >&2
    exit 1
}

# Build host: same requirement as install targets
resolve_build_python311() {
    require_python311 ""
}

# Optional: verify an existing venv was created with 3.11
assert_venv_python311() {
    local venv_python="${1:?venv python path}"
    if [ ! -x "${venv_python}" ]; then
        echo "HATA: Sanal ortam Python'u bulunamadı: ${venv_python}" >&2
        exit 1
    fi
    if ! _python311_version_ok "${venv_python}"; then
        local ver
        ver="$(_python_version_label "${venv_python}")"
        echo "HATA: Sanal ortam Python ${ver} — yeniden oluşturun (python3.11 -m venv ...)." >&2
        exit 1
    fi
}
