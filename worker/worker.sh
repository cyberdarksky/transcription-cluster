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

exec "${VENV_PYTHON}" -m agent.main "$@"
