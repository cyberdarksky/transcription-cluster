#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Kurulu işçi paketini doğrular (çevrimdışı).
# Paket kökünden veya kurulum sonrası çalıştırılabilir.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="/opt/transcription-worker"
MODEL_DIR="/opt/transcription-models/current"
MODEL_ROOT="/opt/transcription-models"
LOG_DIR="/var/log/transcription-worker"
FAILURES=0

pass() { echo "  ✓ $*"; }
fail() { echo "  ✗ $*"; FAILURES=$((FAILURES + 1)); }

echo "=== İşçi Kurulum Doğrulaması ==="
echo ""

# ── Servis ────────────────────────────────────────────────────────────────────
if sudo launchctl print system/com.transcription.worker &>/dev/null; then
    pass "launchd servisi kayıtlı"
else
    fail "launchd servisi bulunamadı"
fi

# ── ffmpeg ────────────────────────────────────────────────────────────────────
if [ -x "${INSTALL_DIR}/bin/ffmpeg" ] && [ -x "${INSTALL_DIR}/bin/ffprobe" ]; then
    pass "ffmpeg: $("${INSTALL_DIR}/bin/ffmpeg" -version 2>&1 | head -n 1)"
else
    fail "ffmpeg/ffprobe eksik (${INSTALL_DIR}/bin/)"
fi

# ffprobe PATH üzerinden erişilebilir mi (downloader.py bunu kullanır)
export PATH="${INSTALL_DIR}/bin:${PATH}"
if command -v ffprobe &>/dev/null; then
    pass "ffprobe PATH üzerinde erişilebilir"
else
    fail "ffprobe PATH üzerinde bulunamadı"
fi

# ── Model ─────────────────────────────────────────────────────────────────────
if [ -L "${MODEL_ROOT}/current" ] || [ -d "${MODEL_ROOT}/current" ]; then
    pass "Model 'current' symlink mevcut"
else
    fail "Model 'current' symlink eksik"
fi

if [ -f "${MODEL_ROOT}/registry.json" ]; then
    pass "registry.json mevcut"
fi

for f in config.json weights.npz; do
    if [ -f "${MODEL_DIR}/${f}" ]; then
        pass "Model dosyası: ${f}"
    else
        fail "Eksik model dosyası: ${f}"
    fi
done

# ── Python ortamı ─────────────────────────────────────────────────────────────
VENV_PYTHON="${INSTALL_DIR}/venv/bin/python3"
if [ -x "${VENV_PYTHON}" ]; then
    pass "Sanal ortam mevcut"
else
    fail "Sanal ortam eksik"
    echo ""
    echo "Doğrulama başarısız (${FAILURES} hata)."
    exit 1
fi

if "${VENV_PYTHON}" -c "import mlx_whisper" 2>/dev/null; then
    pass "mlx-whisper import edilebiliyor"
else
    fail "mlx-whisper import edilemiyor"
fi

# ── worker.sh ─────────────────────────────────────────────────────────────────
if [ -x "${INSTALL_DIR}/worker/worker.sh" ]; then
    pass "worker.sh çalıştırılabilir"
else
    fail "worker.sh eksik veya çalıştırılamıyor"
fi

# ── Kısa model testi (opsiyonel, yavaş) ───────────────────────────────────────
if [ "${SKIP_MODEL_TEST:-}" != "1" ]; then
    echo ""
    echo "  Model yükleme testi (30 sn sürebilir)..."
    if MODEL_PATH="${MODEL_DIR}" "${VENV_PYTHON}" - <<'PY' 2>/dev/null | grep -q MODEL_TEST_OK
import os
import mlx_whisper
import numpy as np

model = os.environ["MODEL_PATH"]
audio = np.zeros(3 * 16000, dtype=np.float32)
mlx_whisper.transcribe(audio, path_or_hf_repo=model, language="tr")
print("MODEL_TEST_OK")
PY
        pass "Model yükleme ve çıkarım başarılı"
    else
        fail "Model testi başarısız (SKIP_MODEL_TEST=1 ile atlanabilir)"
    fi
fi

# ── Log dosyası ───────────────────────────────────────────────────────────────
if [ -f "${LOG_DIR}/worker.log" ] || [ -f "${LOG_DIR}/worker-error.log" ]; then
    pass "Log dosyaları mevcut"
else
    echo "  ℹ Henüz log dosyası yok (servis henüz başlamamış olabilir)"
fi

echo ""
if [ "${FAILURES}" -eq 0 ]; then
    echo "Doğrulama başarılı."
    exit 0
fi

echo "Doğrulama başarısız (${FAILURES} hata)."
echo "Log: tail -f ${LOG_DIR}/worker-error.log"
exit 1
