#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Transkripsiyon İşçi — Kaldırma Betiği
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="/opt/transcription-worker"
LOG_DIR="/var/log/transcription-worker"
LAUNCHD_PLIST="/Library/LaunchDaemons/com.transcription.worker.plist"

echo "=== Transkripsiyon İşçi Kaldırılıyor ==="

if [ -f "${LAUNCHD_PLIST}" ]; then
    sudo launchctl bootout system "${LAUNCHD_PLIST}" 2>/dev/null || true
    sudo rm -f "${LAUNCHD_PLIST}"
    echo "  ✓ launchd servisi kaldırıldı"
fi

if [ -d "${INSTALL_DIR}" ]; then
    read -r -p "Kurulum dizini silinsin mi? (${INSTALL_DIR}) [y/N] " confirm
    if [[ "${confirm}" =~ ^[Yy]$ ]]; then
        sudo rm -rf "${INSTALL_DIR}"
        echo "  ✓ ${INSTALL_DIR} silindi"
    else
        echo "  ℹ ${INSTALL_DIR} korundu"
    fi
fi

read -r -p "Model dizini silinsin mi? (/opt/transcription-models) [y/N] " confirm_model
if [[ "${confirm_model}" =~ ^[Yy]$ ]]; then
    sudo rm -rf /opt/transcription-models
    echo "  ✓ Model dizini silindi"
fi

read -r -p "Log dizini silinsin mi? (${LOG_DIR}) [y/N] " confirm_logs
if [[ "${confirm_logs}" =~ ^[Yy]$ ]]; then
    sudo rm -rf "${LOG_DIR}"
    echo "  ✓ Log dizini silindi"
fi

echo ""
echo "Kaldırma tamamlandı."
