#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Transkripsiyon İşçisi — Başlatma Sarıcısı
#
# Kurulum sonrası yol: /opt/transcription-worker/worker/worker.sh
# launchd veya manuel çalıştırma için ortamı hazırlar.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_DIR="${INSTALL_ROOT}/worker"
VENV_PYTHON="${INSTALL_ROOT}/venv/bin/python3"

export PATH="${INSTALL_ROOT}/bin:${PATH}"

if [ -f "${WORKER_DIR}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${WORKER_DIR}/.env"
    set +a
fi

cd "${WORKER_DIR}"

if [ ! -x "${VENV_PYTHON}" ]; then
    echo "HATA: Sanal ortam bulunamadı: ${VENV_PYTHON}" >&2
    echo "Önce install-worker.sh çalıştırın." >&2
    exit 1
fi

_PY311_LIB=""
for _candidate in \
    "${INSTALL_ROOT}/scripts/lib/python311.sh" \
    "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/lib/python311.sh"; do
    if [ -f "${_candidate}" ]; then
        _PY311_LIB="${_candidate}"
        break
    fi
done
if [ -n "${_PY311_LIB}" ]; then
    # shellcheck disable=SC1091
    source "${_PY311_LIB}"
    assert_venv_python311 "${VENV_PYTHON}"
fi
unset _PY311_LIB _candidate

exec "${VENV_PYTHON}" -m agent.main "$@"
