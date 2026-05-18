#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Transkripsiyon Master — Kaldırma Betiği
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="/opt/transcription-cluster"
DATA_DIR="${TRANSCRIPTION_DATA_DIR:-/opt/transcription-data}"
LOG_DIR="/var/log/transcription"
COORDINATOR_PLIST="/Library/LaunchDaemons/com.transcription.coordinator.plist"
PGWATCHER_PLIST="/Library/LaunchDaemons/com.transcription.pgwatcher.plist"

echo "=== Transkripsiyon Master Kaldırılıyor ==="

for plist in "${COORDINATOR_PLIST}" "${PGWATCHER_PLIST}"; do
    if [ -f "${plist}" ]; then
        sudo launchctl bootout system "${plist}" 2>/dev/null || true
        sudo rm -f "${plist}"
        echo "  ✓ $(basename "${plist}") kaldırıldı"
    fi
done

if [ -d "${INSTALL_DIR}" ]; then
    read -r -p "Kurulum dizini silinsin mi? (${INSTALL_DIR}) [y/N] " confirm
    if [[ "${confirm}" =~ ^[Yy]$ ]]; then
        sudo rm -rf "${INSTALL_DIR}"
        echo "  ✓ ${INSTALL_DIR} silindi"
    else
        echo "  ℹ ${INSTALL_DIR} korundu"
    fi
fi

read -r -p "Veri dizini silinsin mi? (${DATA_DIR}) [y/N] " confirm_data
if [[ "${confirm_data}" =~ ^[Yy]$ ]]; then
    sudo rm -rf "${DATA_DIR}"
    echo "  ✓ Veri dizini silindi"
fi

read -r -p "Log dizini silinsin mi? (${LOG_DIR}) [y/N] " confirm_logs
if [[ "${confirm_logs}" =~ ^[Yy]$ ]]; then
    sudo rm -rf "${LOG_DIR}"
    echo "  ✓ Log dizini silindi"
fi

read -r -p "Postgres.app kaldırılsın m? (/Applications/Postgres.app) [y/N] " confirm_pg
if [[ "${confirm_pg}" =~ ^[Yy]$ ]]; then
    sudo rm -rf /Applications/Postgres.app
    echo "  ✓ Postgres.app kaldırıldı"
fi

echo ""
echo "Kaldırma tamamlandı."
