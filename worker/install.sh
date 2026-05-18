#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# İşçi Ajanı Kurulum Betiği
# Apple Silicon Mac Studio (macOS 14+) için
#
# Kullanım:
#   chmod +x install.sh
#   ./install.sh
#
# Gereksinimler:
#   - macOS 14+ (Sonoma)
#   - Python 3.11+ (Homebrew veya python.org)
#   - Whisper Medium MLX modeli (/opt/transcription-models/whisper-medium-mlx)
#     NOT: Model koordinatör paketinden kopyalanmalıdır (internette değil)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="/opt/transcription-worker"
LOG_DIR="/var/log/transcription-worker"
LAUNCHD_PLIST="/Library/LaunchDaemons/com.transcription.worker.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Transkripsiyon İşçisi Kurulumu ==="
echo ""

# ── 1. Gereksinim kontrolleri ─────────────────────────────────────────────────
echo "[1/6] Gereksinimler kontrol ediliyor..."

if [ "$(uname -m)" != "arm64" ]; then
    echo "HATA: Apple Silicon (arm64) gereklidir. Mevcut: $(uname -m)"
    exit 1
fi

MAJOR=$(sw_vers -productVersion | cut -d. -f1)
if [ "$MAJOR" -lt 14 ]; then
    echo "HATA: macOS 14+ gereklidir."
    exit 1
fi

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../scripts/lib/python311.sh"
require_python311 ""
echo "  ✓ Python $("$PYTHON" --version 2>&1)"

MODEL_PATH="/opt/transcription-models/whisper-medium-mlx"
if [ ! -d "$MODEL_PATH" ]; then
    echo "UYARI: Model bulunamadı: $MODEL_PATH"
    echo "  Koordinatör kurulum paketinden modeli kopyalayın:"
    echo "  sudo cp -r <koordinatör-paketi>/models/whisper-medium-mlx /opt/transcription-models/"
fi

echo "  ✓ macOS $(sw_vers -productVersion) (arm64)"

# ── 2. Dizinler ────────────────────────────────────────────────────────────────
echo "[2/6] Dizinler oluşturuluyor..."
sudo mkdir -p "$INSTALL_DIR"/{agent,venv}
sudo mkdir -p "$LOG_DIR"
sudo mkdir -p /tmp/transcription-jobs
sudo chown -R "$(whoami)":"$(id -gn)" "$INSTALL_DIR" "$LOG_DIR"

# ── 3. Sanal ortam ─────────────────────────────────────────────────────────────
echo "[3/6] Python sanal ortamı oluşturuluyor..."
"$PYTHON" -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

# ── 4. Bağımlılıklar ───────────────────────────────────────────────────────────
echo "[4/6] Bağımlılıklar yükleniyor..."
if [ -d "$SCRIPT_DIR/wheelhouse" ]; then
    # Çevrimdışı kurulum — yerel wheelhouse'dan
    pip install --no-index --find-links="$SCRIPT_DIR/wheelhouse" \
        -r "$SCRIPT_DIR/requirements.txt" --quiet
    echo "  ✓ Bağımlılıklar çevrimdışı olarak yüklendi"
else
    # Çevrimiçi kurulum (paket hazırlama sırasında)
    pip install -r "$SCRIPT_DIR/requirements.txt" --quiet
    echo "  ✓ Bağımlılıklar çevrimiçi olarak yüklendi"
fi

# ── 5. Uygulama dosyaları ──────────────────────────────────────────────────────
echo "[5/6] Uygulama dosyaları kopyalanıyor..."
rsync -av --exclude="__pycache__" --exclude="*.pyc" --exclude=".venv" \
    "$SCRIPT_DIR/agent/" "$INSTALL_DIR/agent/" --quiet

# .env yapılandırma dosyası
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$INSTALL_DIR/.env"
    echo "  ✓ .env dosyası oluşturuldu — düzenleme gerekebilir"
else
    echo "  ℹ .env zaten mevcut, korunuyor"
fi

# ── 6. launchd servisi ────────────────────────────────────────────────────────
echo "[6/6] Sistem servisi kuruluyor..."

HOSTNAME=$(hostname)

sudo tee "$LAUNCHD_PLIST" > /dev/null << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.transcription.worker</string>

    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_DIR/venv/bin/python3</string>
        <string>-m</string>
        <string>agent.main</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>$INSTALL_DIR</string>
    </dict>

    <key>StandardOutPath</key>
    <string>$LOG_DIR/worker.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/worker-error.log</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>Crashed</key>
        <true/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
EOF

sudo launchctl bootout system "$LAUNCHD_PLIST" 2>/dev/null || true
sudo launchctl bootstrap system "$LAUNCHD_PLIST"

# ── Tamamlandı ─────────────────────────────────────────────────────────────────
echo ""
echo "=== Kurulum Tamamlandı! ==="
echo ""
echo "Servis durumu : sudo launchctl list com.transcription.worker"
echo "Loglar        : tail -f $LOG_DIR/worker.log"
echo ""
echo "Koordinatör IP'sini yapılandırmak için:"
echo "  nano $INSTALL_DIR/.env"
echo "  # COORDINATOR_HOST=192.168.1.101"
echo "  sudo launchctl kickstart -k system/com.transcription.worker"
echo ""
echo "Koordinatör yoksa mDNS ile otomatik keşif aktif olacak."
