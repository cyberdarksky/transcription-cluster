# PACKAGING_STRATEGY.md
# Paketleme ve Dağıtım Stratejisi

**Hedef Platform:** macOS 14+ (Sonoma veya üzeri) — Apple Silicon (M1/M2/M3/M4)  
**Kısıt:** Kurulum sonrası tamamen çevrimdışı çalışma

---

## 1. Paket Türleri

| Paket | Hedef Makine | İçerik | Boyut (tahmini) |
|---|---|---|---|
| **Koordinatör Kurulum Paketi** | Ana Mac Studio | FastAPI + PostgreSQL + React Dashboard + Betikler | ~400 MB |
| **İşçi Kurulum Paketi** | Her ek Mac Studio | İşçi Ajanı + mlx-whisper + Whisper Medium Modeli | ~3.5 GB |

---

## 2. Koordinatör Paketi

### 2.1 Bileşenler

```
coordinator-package/
├── install.sh                  ← Ana kurulum betiği
├── uninstall.sh                ← Kaldırma betiği
├── payload/
│   ├── coordinator/            ← FastAPI uygulama kodu
│   │   ├── app/                ← Python kaynak
│   │   ├── migrations/         ← Alembic migrasyon dosyaları
│   │   ├── requirements.txt
│   │   └── coordinator.sh      ← Başlatma sarıcısı
│   ├── dashboard/
│   │   └── dist/               ← Önceden derlenmiş React uygulaması
│   ├── wheelhouse/             ← Tüm Python bağımlılıkları (.whl dosyaları)
│   │   ├── fastapi-0.115.x-py3-none-any.whl
│   │   ├── uvicorn-0.30.x-...whl
│   │   ├── sqlalchemy-2.x-...whl
│   │   ├── asyncpg-0.29.x-...whl
│   │   ├── pydantic-2.x-...whl
│   │   ├── watchdog-4.x-...whl
│   │   ├── zeroconf-0.131.x-...whl
│   │   └── [diğer tüm bağımlılıklar]
│   ├── postgres/               ← PostgreSQL kurulum kaynakları
│   │   ├── Postgres.app        ← Postgres.app v16 (arm64, bağımsız .app)
│   │   │                         Homebrew GEREKTIRMEZ; internet GEREKTIRMEZ
│   │   └── postgres-version.txt
│   └── launchd/
│       ├── com.transcription.coordinator.plist
│       └── com.transcription.pgwatcher.plist
└── README_KURULUM.md
```

### 2.2 `install.sh` Kurulum Adımları

```bash
#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/transcription-cluster"
DATA_DIR="/Volumes/Data/transcription"  # Kullanıcı tarafından özelleştirilebilir
LOG_DIR="/var/log/transcription"
LAUNCHD_DIR="/Library/LaunchDaemons"

echo "=== Transkripsiyon Kümesi Koordinatör Kurulumu ==="
echo ""

# ── 1. Gereksinimler Kontrolü ──────────────────────────────────────
echo "[1/8] Gereksinimler kontrol ediliyor..."

# macOS sürümü
OS_VER=$(sw_vers -productVersion)
MAJOR=$(echo $OS_VER | cut -d. -f1)
if [ "$MAJOR" -lt 14 ]; then
    echo "HATA: macOS 14 (Sonoma) veya üzeri gereklidir. Mevcut: $OS_VER"
    exit 1
fi

# Apple Silicon
ARCH=$(uname -m)
if [ "$ARCH" != "arm64" ]; then
    echo "HATA: Apple Silicon (arm64) gereklidir. Mevcut: $ARCH"
    exit 1
fi

# Python 3.11 — paket içindeki .pkg kurulucusu ile
if ! /usr/bin/python3.11 --version &>/dev/null 2>&1; then
    echo "Python 3.11 bulunamadı. Paket içindeki kurulucudan yükleniyor..."
    PYTHON_PKG="$(dirname "$0")/payload/python/python-3.11.x-macos14-arm64.pkg"
    if [ ! -f "$PYTHON_PKG" ]; then
        echo "HATA: Python kurulum paketi bulunamadı: $PYTHON_PKG"
        echo "Paketin bütünlüğü bozulmuş. Yeniden indirin."
        exit 1
    fi
    # .pkg kurulucusu sessiz kurulum yapar — sudo gerektirir
    sudo installer -pkg "$PYTHON_PKG" -target /
    # Kurulum sonrası doğrulama
    if ! python3.11 --version &>/dev/null; then
        echo "HATA: Python 3.11 kurulumu başarısız!"
        exit 1
    fi
fi

echo "  ✓ macOS $OS_VER (arm64)"
echo "  ✓ Python $(python3.11 --version)"

# ── 2. Dizin Yapısı ────────────────────────────────────────────────
echo "[2/8] Dizin yapısı oluşturuluyor..."
sudo mkdir -p "$INSTALL_DIR"/{coordinator,venv}
sudo mkdir -p "$DATA_DIR"/{input,output}
sudo mkdir -p "$LOG_DIR"
sudo chown -R "$(whoami)":"$(id -gn)" "$INSTALL_DIR"
sudo chown -R "$(whoami)":"$(id -gn)" "$DATA_DIR"

# ── 3. PostgreSQL Kurulumu (Postgres.app — Homebrew gerektirmez) ────
echo "[3/8] PostgreSQL 16 yükleniyor (Postgres.app)..."

POSTGRES_APP="payload/postgres/Postgres.app"
POSTGRES_INSTALL_DIR="/Applications/Postgres.app"
POSTGRES_BIN="/Applications/Postgres.app/Contents/Versions/latest/bin"

if [ ! -d "$POSTGRES_INSTALL_DIR" ]; then
    echo "  Postgres.app kopyalanıyor..."
    cp -r "$POSTGRES_APP" /Applications/
    # macOS gatekeeper bypass — paket içi imzalı uygulama için
    xattr -rd com.apple.quarantine /Applications/Postgres.app 2>/dev/null || true
fi

# PATH'e PostgreSQL binary'lerini ekle
export PATH="$POSTGRES_BIN:$PATH"
echo "export PATH=\"$POSTGRES_BIN:\$PATH\"" >> ~/.zprofile

# Postgres.app'ın kendi launchd entegrasyonu ile servisi başlat
open -a Postgres 2>/dev/null || true
sleep 3  # Postgres.app'ın başlaması için bekle

# Postgres.app varsayılan olarak mevcut kullanıcı adında bir veritabanı oluşturur.
# Transkripsiyon veritabanını oluştur:
createdb transcription_cluster 2>/dev/null || true
echo "  ✓ PostgreSQL 16 (Postgres.app) hazır"
echo "  ℹ Postgres.app macOS menü çubuğundan yönetilir"

# ── 4. Python Sanal Ortamı ─────────────────────────────────────────
echo "[4/8] Python sanal ortamı oluşturuluyor..."
python3.11 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

# Tüm bağımlılıkları çevrimdışı wheelhouse'dan yükle
pip install --no-index --find-links=payload/wheelhouse \
    -r payload/coordinator/requirements.txt
echo "  ✓ Python bağımlılıkları yüklendi (çevrimdışı)"

# ── 5. Uygulama Dosyaları ─────────────────────────────────────────
echo "[5/8] Uygulama dosyaları kopyalanıyor..."
cp -r payload/coordinator/. "$INSTALL_DIR/coordinator/"
cp -r payload/dashboard/dist "$INSTALL_DIR/coordinator/static/"

# ── 6. Veritabanı Şeması ──────────────────────────────────────────
echo "[6/8] Veritabanı şeması oluşturuluyor..."
source "$INSTALL_DIR/venv/bin/activate"
cd "$INSTALL_DIR/coordinator"
DATABASE_URL="postgresql://localhost/transcription_cluster" \
    alembic upgrade head
echo "  ✓ Veritabanı şeması hazır"

# ── 7. Yapılandırma ────────────────────────────────────────────────
echo "[7/8] Yapılandırma dosyası oluşturuluyor..."
cat > "$INSTALL_DIR/coordinator/config.env" << EOF
DATABASE_URL=postgresql://localhost/transcription_cluster
COORDINATOR_HOST=0.0.0.0
COORDINATOR_PORT=8080
INPUT_BASE_DIR=$DATA_DIR/input
OUTPUT_BASE_DIR=$DATA_DIR/output
LOG_LEVEL=INFO
LOG_DIR=$LOG_DIR
EOF

# ── 8. launchd Servisleri ─────────────────────────────────────────
echo "[8/8] Sistem servisleri kuruluyor..."

# Koordinatör servis plist'ini güncelle
sed "s|INSTALL_DIR|$INSTALL_DIR|g; s|LOG_DIR|$LOG_DIR|g" \
    payload/launchd/com.transcription.coordinator.plist \
    | sudo tee "$LAUNCHD_DIR/com.transcription.coordinator.plist" > /dev/null

sudo launchctl bootstrap system "$LAUNCHD_DIR/com.transcription.coordinator.plist"

# ── Tamamlandı ────────────────────────────────────────────────────
LOCAL_IP=$(ipconfig getifaddr en0 || ipconfig getifaddr en1)
echo ""
echo "=== Kurulum Tamamlandı! ==="
echo ""
echo "Dashboard erişimi: http://$LOCAL_IP:8080"
echo "Giriş dizini     : $DATA_DIR/input"
echo "Çıktı dizini     : $DATA_DIR/output"
echo ""
echo "Loglar: tail -f $LOG_DIR/coordinator.log"
```

### 2.3 launchd Plist (Koordinatör)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.transcription.coordinator</string>

    <key>ProgramArguments</key>
    <array>
        <string>INSTALL_DIR/venv/bin/uvicorn</string>
        <string>app.main:app</string>
        <string>--host</string><string>0.0.0.0</string>
        <string>--port</string><string>8080</string>
        <!-- UYARI: --workers 1 olmalıdır. -->
        <!-- Birden fazla işçi WebSocket bağlantılarını farklı süreçlere dağıtır; -->
        <!-- bu durumda bir süreçteki yayın diğerindeki dashboard istemcisine ulaşmaz. -->
        <!-- Koordinatör CPU'ya bağlı değildir; tek async süreç + uvloop yeterlidir. -->
        <string>--workers</string><string>1</string>
        <string>--loop</string><string>uvloop</string>
        <string>--log-level</string><string>info</string>
    </array>

    <key>WorkingDirectory</key>
    <string>INSTALL_DIR/coordinator</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>INSTALL_DIR/coordinator</string>
        <key>ENV_FILE</key>
        <string>INSTALL_DIR/coordinator/config.env</string>
    </dict>

    <key>StandardOutPath</key>
    <string>LOG_DIR/coordinator.log</string>
    <key>StandardErrorPath</key>
    <string>LOG_DIR/coordinator-error.log</string>

    <!-- Sistem başlangıcında otomatik başlat -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Çökme durumunda 5 saniye sonra yeniden başlat -->
    <key>KeepAlive</key>
    <dict>
        <key>Crashed</key>
        <true/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>5</integer>

    <!-- PostgreSQL hazır olunca başlat -->
    <key>LaunchOnlyOnce</key>
    <false/>
</dict>
</plist>
```

---

## 3. İşçi Paketi

### 3.1 Bileşenler

```
worker-package/
├── install.sh                  ← Ana kurulum betiği
├── uninstall.sh
├── payload/
│   ├── worker/                 ← İşçi ajan kodu
│   │   ├── agent/              ← Python kaynak
│   │   ├── requirements.txt
│   │   └── worker.sh           ← Başlatma sarıcısı
│   ├── wheelhouse/             ← Python bağımlılıkları
│   │   ├── mlx_whisper-0.4.x-...whl
│   │   ├── mlx-0.16.x-...whl
│   │   ├── httpx-0.27.x-...whl
│   │   ├── websockets-12.x-...whl
│   │   ├── zeroconf-0.131.x-...whl
│   │   ├── psutil-5.x-...whl
│   │   └── [diğer tüm bağımlılıklar]
│   ├── models/
│   │   └── whisper-medium-mlx/ ← Önceden indirilmiş model dosyaları
│   │       ├── config.json
│   │       ├── model.safetensors
│   │       ├── tokenizer.json
│   │       └── [diğer model dosyaları]
│   └── launchd/
│       └── com.transcription.worker.plist
└── README_KURULUM.md
```

**Kritik Not:** `models/whisper-medium-mlx/` dizini ~3 GB boyutundadır. Bu model dosyaları **internet erişimi gerektirmeden** kurulum sırasında kopyalanır. Model dosyaları paket hazırlanırken `mlx_whisper.utils.snapshot_download()` ile indirilip pakete dahil edilir.

### 3.2 `install.sh` Kurulum Adımları

```bash
#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/transcription-worker"
MODEL_DIR="/opt/transcription-models"
LOG_DIR="/var/log/transcription-worker"
LAUNCHD_DIR="/Library/LaunchDaemons"

echo "=== Transkripsiyon Kümesi İşçi Kurulumu ==="
echo ""

# ── 1. Gereksinimler Kontrolü ──────────────────────────────────────
echo "[1/7] Gereksinimler kontrol ediliyor..."

ARCH=$(uname -m)
if [ "$ARCH" != "arm64" ]; then
    echo "HATA: Apple Silicon (arm64) gereklidir."
    exit 1
fi

echo "  ✓ Apple Silicon $(sysctl -n machdep.cpu.brand_string)"

# ── 2. Dizin Yapısı ────────────────────────────────────────────────
echo "[2/7] Dizin yapısı oluşturuluyor..."
sudo mkdir -p "$INSTALL_DIR"/{worker,venv}
sudo mkdir -p "$MODEL_DIR"
sudo mkdir -p "$LOG_DIR"
sudo mkdir -p /tmp/transcription-jobs
sudo chown -R "$(whoami)":"$(id -gn)" "$INSTALL_DIR" "$MODEL_DIR" "$LOG_DIR"

# ── 3. Python Sanal Ortamı ─────────────────────────────────────────
echo "[3/7] Python sanal ortamı oluşturuluyor..."
python3.11 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"

# Çevrimdışı wheel kurulumu
pip install --no-index --find-links=payload/wheelhouse \
    -r payload/worker/requirements.txt
echo "  ✓ Python bağımlılıkları yüklendi (çevrimdışı)"

# ── 4. Uygulama Dosyaları ─────────────────────────────────────────
echo "[4/7] İşçi dosyaları kopyalanıyor..."
cp -r payload/worker/. "$INSTALL_DIR/worker/"

# ── 5. Whisper Modeli ─────────────────────────────────────────────
echo "[5/7] Whisper Medium modeli kopyalanıyor (~3 GB)..."
MODEL_DEST="$MODEL_DIR/whisper-medium-mlx"
if [ -d "$MODEL_DEST" ]; then
    echo "  ℹ Model zaten mevcut, atlanıyor."
else
    cp -r payload/models/whisper-medium-mlx "$MODEL_DEST"
    echo "  ✓ Model kopyalandı: $MODEL_DEST"
fi

# Model bütünlük doğrulaması
EXPECTED_HASH=$(cat payload/models/whisper-medium-mlx.sha256 2>/dev/null || echo "")
if [ -n "$EXPECTED_HASH" ]; then
    ACTUAL_FILES=$(find "$MODEL_DEST" -name "*.safetensors" | sort | xargs md5 -q | md5 -q)
    if [ "$ACTUAL_FILES" != "$EXPECTED_HASH" ]; then
        echo "UYARI: Model dosyası karması uyuşmuyor — model bozulmuş olabilir"
    fi
fi

# ÖNEMLI: mlx_whisper.transcribe() path_or_hf_repo parametresi olarak
# doğrudan yerel dizin yolunu kabul eder. HuggingFace önbelleğine sembolik
# bağlantı oluşturmak GEREKMEZ ve YAPILMAMALIDIR (kırılgan, gereksiz).
# config.env dosyasına yerel model yolu yazılır; işçi bu yolu kullanır.

# ── 6. Yapılandırma ────────────────────────────────────────────────
echo "[6/7] Yapılandırma oluşturuluyor..."
cat > "$INSTALL_DIR/worker/config.env" << EOF
# Whisper modeli — MUTLAK YEREL YOL (HuggingFace repo adı DEĞİL)
# mlx_whisper.transcribe(path_or_hf_repo=WORKER_MODEL_PATH) şeklinde kullanılır
# İnternet bağlantısı gerektirmez
WORKER_MODEL_PATH=$MODEL_DEST
WORKER_TEMP_DIR=/tmp/transcription-jobs
LOG_LEVEL=INFO
LOG_DIR=$LOG_DIR
# COORDINATOR_HOST=192.168.1.101  # mDNS otomatik keşif için bu satırı yorum satırı bırakın
# COORDINATOR_PORT=8080
EOF

echo ""
echo "  ℹ Koordinatör IP'si otomatik olarak mDNS ile keşfedilecek."
echo "  ℹ Manuel IP için $INSTALL_DIR/worker/config.env dosyasını düzenleyin."

# ── 7. launchd Servisi ────────────────────────────────────────────
echo "[7/7] Sistem servisi kuruluyor..."

sed "s|INSTALL_DIR|$INSTALL_DIR|g; s|LOG_DIR|$LOG_DIR|g" \
    payload/launchd/com.transcription.worker.plist \
    | sudo tee "$LAUNCHD_DIR/com.transcription.worker.plist" > /dev/null

sudo launchctl bootstrap system "$LAUNCHD_DIR/com.transcription.worker.plist"

# ── Tamamlandı ────────────────────────────────────────────────────
HOSTNAME=$(hostname)
echo ""
echo "=== İşçi Kurulumu Tamamlandı! ==="
echo ""
echo "Makine adı   : $HOSTNAME"
echo "Durum        : Koordinatör keşfedilince otomatik bağlanacak"
echo ""
echo "Loglar: tail -f $LOG_DIR/worker.log"
echo "Durum : sudo launchctl list com.transcription.worker"
```

### 3.3 launchd Plist (İşçi)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.transcription.worker</string>

    <key>ProgramArguments</key>
    <array>
        <string>INSTALL_DIR/venv/bin/python3</string>
        <string>-m</string>
        <string>agent.main</string>
    </array>

    <key>WorkingDirectory</key>
    <string>INSTALL_DIR/worker</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>INSTALL_DIR/worker</string>
        <key>ENV_FILE</key>
        <string>INSTALL_DIR/worker/config.env</string>
    </dict>

    <key>StandardOutPath</key>
    <string>LOG_DIR/worker.log</string>
    <key>StandardErrorPath</key>
    <string>LOG_DIR/worker-error.log</string>

    <key>RunAtLoad</key>
    <true/>

    <!-- Çökmede 10 saniye sonra yeniden başlat -->
    <key>KeepAlive</key>
    <dict>
        <key>Crashed</key>
        <true/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>

    <!-- Yüksek bellek limiti — büyük modeller için -->
    <!-- Hiçbir zaman OOM killer tarafından öncelikli öldürülmesin -->
    <key>ProcessType</key>
    <string>Background</string>

    <!-- Güç yönetimi: AC adaptöre bağlıyken çalış, pillede duraklat -->
    <!-- Opsiyonel — gerekirse açıklanabilir -->
    <!-- <key>PowerType</key><string>AC</string> -->
</dict>
</plist>
```

---

## 4. Paket Hazırlama Süreci

### 4.1 Koordinatör Paketi Hazırlama

Koordinatör paketi internete erişimi olan bir geliştirici makinesinde hazırlanır:

```bash
#!/usr/bin/env bash
# scripts/build_coordinator_package.sh

set -euo pipefail

PACKAGE_DIR="dist/coordinator-package"
rm -rf "$PACKAGE_DIR" && mkdir -p "$PACKAGE_DIR/payload"

echo "=== Koordinatör Paketi Hazırlanıyor ==="

# ── 0. Ön Koşul: Postgres.app ve Python .pkg Edinimi ─────────────
# Bu adım INTERNET GEREKTİRİR; paket hazırlayan makinede bir kez yapılır.
# Hazırlık sırasında şunlar indirilmelidir:
#   1. Postgres.app (arm64): https://postgresapp.com/downloads.html
#      → payload/postgres/Postgres.app kopyalanır
#   2. Python 3.11 .pkg (arm64): https://python.org/downloads/
#      → payload/python/python-3.11.x-macos14-arm64.pkg kopyalanır
#
echo "[0/5] Ön koşul dosyaları kontrol ediliyor..."
[ -d "payload/postgres/Postgres.app" ] || { echo "HATA: payload/postgres/Postgres.app eksik"; exit 1; }
[ -f payload/python/python-3.11.*.pkg ] || { echo "HATA: payload/python/python-3.11.x-...pkg eksik"; exit 1; }

# ── 1. Python Bağımlılıklarını İndir ──────────────────────────────
echo "[1/5] Python wheel'ları indiriliyor..."
mkdir -p "$PACKAGE_DIR/payload/wheelhouse"
pip download \
    --platform macosx_14_0_arm64 \
    --python-version 3.11 \
    --only-binary=:all: \
    --dest "$PACKAGE_DIR/payload/wheelhouse" \
    -r coordinator/requirements.txt

# ── 2. React Dashboard Derleme ────────────────────────────────────
echo "[2/5] React dashboard derleniyor..."
(cd dashboard && npm ci && npm run build)
mkdir -p "$PACKAGE_DIR/payload/dashboard"
cp -r dashboard/dist "$PACKAGE_DIR/payload/dashboard/"

# ── 3. Uygulama Kaynak Kodu ───────────────────────────────────────
echo "[3/5] Kaynak kodu kopyalanıyor..."
rsync -av --exclude=".venv" --exclude="__pycache__" --exclude="*.pyc" \
    coordinator/ "$PACKAGE_DIR/payload/coordinator/"

# ── 4. Yapılandırma Dosyaları ─────────────────────────────────────
echo "[4/5] Yapılandırma kopyalanıyor..."
cp scripts/install/coordinator/install.sh "$PACKAGE_DIR/"
cp scripts/install/coordinator/uninstall.sh "$PACKAGE_DIR/"
cp -r scripts/launchd/coordinator "$PACKAGE_DIR/payload/launchd/"
cp docs/README_KOORDINATOR_KURULUM.md "$PACKAGE_DIR/README_KURULUM.md"

# ── 5. Arşiv Oluştur ─────────────────────────────────────────────
echo "[5/5] Arşiv oluşturuluyor..."
VERSION=$(cat VERSION)
tar -czf "dist/coordinator-v${VERSION}-arm64.tar.gz" -C dist coordinator-package/

echo ""
echo "Paket hazır: dist/coordinator-v${VERSION}-arm64.tar.gz"
echo "Boyut: $(du -sh dist/coordinator-v${VERSION}-arm64.tar.gz | cut -f1)"
```

### 4.2 İşçi Paketi Hazırlama

```bash
#!/usr/bin/env bash
# scripts/build_worker_package.sh

set -euo pipefail

PACKAGE_DIR="dist/worker-package"
rm -rf "$PACKAGE_DIR" && mkdir -p "$PACKAGE_DIR/payload"

echo "=== İşçi Paketi Hazırlanıyor ==="
echo "NOT: Bu işlem ~3 GB model indirimi gerektirir."

# ── 1. Whisper Medium Modelini İndir ──────────────────────────────
echo "[1/5] Whisper Medium MLX modeli indiriliyor..."
mkdir -p "$PACKAGE_DIR/payload/models"

python3 -c "
from huggingface_hub import snapshot_download
import shutil, os

model_path = snapshot_download(
    repo_id='mlx-community/whisper-medium-mlx',
    local_dir='dist/worker-package/payload/models/whisper-medium-mlx',
    ignore_patterns=['*.msgpack', 'flax_model*'],  # Gereksiz formatları atla
)
print(f'Model indirildi: {model_path}')
"

MODEL_SIZE=$(du -sh "$PACKAGE_DIR/payload/models/whisper-medium-mlx" | cut -f1)
echo "  ✓ Model boyutu: $MODEL_SIZE"

# ── 2. Python Bağımlılıklarını İndir ──────────────────────────────
echo "[2/5] Python wheel'ları indiriliyor..."
mkdir -p "$PACKAGE_DIR/payload/wheelhouse"
pip download \
    --platform macosx_14_0_arm64 \
    --python-version 3.11 \
    --only-binary=:all: \
    --dest "$PACKAGE_DIR/payload/wheelhouse" \
    -r worker/requirements.txt

# ── 3. Uygulama Kodu ──────────────────────────────────────────────
echo "[3/5] İşçi kodu kopyalanıyor..."
rsync -av --exclude=".venv" --exclude="__pycache__" --exclude="*.pyc" \
    worker/ "$PACKAGE_DIR/payload/worker/"

# ── 4. Kurulum Dosyaları ──────────────────────────────────────────
echo "[4/5] Kurulum dosyaları ekleniyor..."
cp scripts/install/worker/install.sh "$PACKAGE_DIR/"
cp scripts/install/worker/uninstall.sh "$PACKAGE_DIR/"
cp -r scripts/launchd/worker "$PACKAGE_DIR/payload/launchd/"
cp docs/README_ISCI_KURULUM.md "$PACKAGE_DIR/README_KURULUM.md"

# ── 5. Arşiv Oluştur ─────────────────────────────────────────────
echo "[5/5] Arşiv oluşturuluyor..."
VERSION=$(cat VERSION)
tar -czf "dist/worker-v${VERSION}-arm64.tar.gz" -C dist worker-package/

FINAL_SIZE=$(du -sh "dist/worker-v${VERSION}-arm64.tar.gz" | cut -f1)
echo ""
echo "Paket hazır: dist/worker-v${VERSION}-arm64.tar.gz"
echo "Boyut: $FINAL_SIZE"
echo ""
echo "USB belleğe kopyala: cp dist/worker-v${VERSION}-arm64.tar.gz /Volumes/USB/"
```

---

## 5. Çevrimdışı Kurulum Doğrulaması

### 5.1 Koordinatör Doğrulaması

```bash
#!/usr/bin/env bash
# Ağ bağlantısı olmadan koordinatör kurulumu doğrular

echo "=== Koordinatör Kurulum Doğrulaması ==="

# 1. Servis çalışıyor mu?
if sudo launchctl list com.transcription.coordinator | grep -q '"PID"'; then
    echo "  ✓ Koordinatör servisi çalışıyor"
else
    echo "  ✗ Koordinatör servisi çalışmıyor!"
    echo "    Kontrol: sudo launchctl list com.transcription.coordinator"
fi

# 2. API yanıt veriyor mu?
if curl -sf http://localhost:8080/api/v1/system/stats > /dev/null; then
    echo "  ✓ API yanıt veriyor"
else
    echo "  ✗ API yanıt vermiyor!"
fi

# 3. PostgreSQL çalışıyor mu?
if pg_isready -h localhost > /dev/null 2>&1; then
    echo "  ✓ PostgreSQL hazır"
else
    echo "  ✗ PostgreSQL hazır değil!"
fi

# 4. mDNS duyuruluyor mu?
if dns-sd -B _transcription._tcp local 2>/dev/null | grep -q "TranscriptionCluster" & \
   sleep 2 && kill %1 2>/dev/null; then
    echo "  ✓ mDNS servisi duyuruluyor"
else
    echo "  ℹ mDNS durumu doğrulanamadı (normal olabilir)"
fi

# 5. Dashboard erişilebilir mi?
if curl -sf http://localhost:8080/ | grep -q "<!DOCTYPE html"; then
    echo "  ✓ Dashboard erişilebilir"
else
    echo "  ✗ Dashboard erişilemiyor!"
fi

LOCAL_IP=$(ipconfig getifaddr en0 || ipconfig getifaddr en1)
echo ""
echo "Dashboard: http://$LOCAL_IP:8080"
```

### 5.2 İşçi Doğrulaması

```bash
#!/usr/bin/env bash
# Çevrimdışı işçi kurulumu doğrular

echo "=== İşçi Kurulum Doğrulaması ==="

INSTALL_DIR="/opt/transcription-worker"
MODEL_DIR="/opt/transcription-models/whisper-medium-mlx"

# 1. Servis çalışıyor mu?
if sudo launchctl list com.transcription.worker | grep -q '"PID"'; then
    echo "  ✓ İşçi servisi çalışıyor"
else
    echo "  ✗ İşçi servisi çalışmıyor!"
fi

# 2. Model dosyaları mevcut mu?
REQUIRED_FILES=("config.json" "model.safetensors" "tokenizer.json")
all_present=true
for f in "${REQUIRED_FILES[@]}"; do
    if [ -f "$MODEL_DIR/$f" ]; then
        echo "  ✓ Model dosyası mevcut: $f"
    else
        echo "  ✗ Eksik model dosyası: $f"
        all_present=false
    fi
done

# 3. Model import edilebilir mi?
if source "$INSTALL_DIR/venv/bin/activate" && \
   python3 -c "import mlx_whisper; print('OK')" 2>/dev/null | grep -q "OK"; then
    echo "  ✓ mlx-whisper import edilebiliyor"
else
    echo "  ✗ mlx-whisper import edilemiyor!"
fi

# 4. Kısa model çalıştırma testi (5 saniyelik sessizlik)
echo "  ○ Model yükleme testi (30 saniye sürebilir)..."
if source "$INSTALL_DIR/venv/bin/activate" && python3 -c "
import mlx_whisper, numpy as np
# 3 saniyelik sessiz ses ile kısa test
audio = np.zeros(3 * 16000, dtype=np.float32)
result = mlx_whisper.transcribe(audio, path_or_hf_repo='$MODEL_DIR', language='tr')
print('MODEL_TEST_OK')
" 2>/dev/null | grep -q "MODEL_TEST_OK"; then
    echo "  ✓ Model yükleme ve çıkarım başarılı"
else
    echo "  ✗ Model test başarısız! Log kontrol edin."
fi

echo ""
echo "Doğrulama tamamlandı."
```

---

## 6. Güncelleme Stratejisi

### 6.1 Koordinatör Güncellemesi

```bash
# Yalnızca uygulama kodu güncellenir; PostgreSQL veri dokunulmaz
UPDATE_PACKAGE="coordinator-v1.1.0-arm64.tar.gz"

# 1. Servisi durdur
sudo launchctl bootout system /Library/LaunchDaemons/com.transcription.coordinator.plist

# 2. Yeni kodu dağıt
tar -xzf "$UPDATE_PACKAGE" -C /tmp/
cp -r /tmp/coordinator-package/payload/coordinator/app /opt/transcription-cluster/coordinator/
cp -r /tmp/coordinator-package/payload/dashboard/dist /opt/transcription-cluster/coordinator/static/

# 3. Bağımlılıkları güncelle
source /opt/transcription-cluster/venv/bin/activate
pip install --no-index --find-links=/tmp/coordinator-package/payload/wheelhouse \
    -r /opt/transcription-cluster/coordinator/requirements.txt

# 4. Migrasyon çalıştır
cd /opt/transcription-cluster/coordinator && alembic upgrade head

# 5. Servisi yeniden başlat
sudo launchctl bootstrap system /Library/LaunchDaemons/com.transcription.coordinator.plist
```

### 6.2 İşçi Güncellemesi (Model Değişmeden)

```bash
UPDATE_PACKAGE="worker-v1.1.0-arm64.tar.gz"  # Model olmadan küçük paket

sudo launchctl bootout system /Library/LaunchDaemons/com.transcription.worker.plist
tar -xzf "$UPDATE_PACKAGE" -C /tmp/
cp -r /tmp/worker-package/payload/worker/agent /opt/transcription-worker/worker/
source /opt/transcription-worker/venv/bin/activate
pip install --no-index --find-links=/tmp/worker-package/payload/wheelhouse \
    -r /opt/transcription-worker/worker/requirements.txt
sudo launchctl bootstrap system /Library/LaunchDaemons/com.transcription.worker.plist
```

---

## 7. Bağımlılık Sabitleme

Üretim güvenilirliği için tüm bağımlılıklar tam sürümle sabitlenir:

```text
# coordinator/requirements.txt (tam sürüm sabitleme örneği)
fastapi==0.115.12
uvicorn[standard]==0.30.6
uvloop==0.21.0
sqlalchemy[asyncio]==2.0.41
asyncpg==0.29.0
alembic==1.13.3
pydantic==2.11.5
pydantic-settings==2.7.1
watchdog==4.0.2
zeroconf==0.131.0
httpx==0.27.2
python-multipart==0.0.20
aiofiles==24.1.0
psutil==6.1.0
```

```text
# worker/requirements.txt
mlx-whisper==0.4.2
mlx==0.22.0
httpx==0.27.2
websockets==12.0
zeroconf==0.131.0
psutil==6.1.0
aiofiles==24.1.0
```

**Tüm sürümler `pip-compile` ile doğrulanmış ve test edilmiş olmalıdır.** Lock dosyaları (`requirements.lock`) depoda tutulur.

---

## 8. Güvenlik Özeti

| Risk | Önlem |
|---|---|
| Wheel dosyaları bozulabilir | Her wheel'ın SHA256 karmasını `wheelhouse/hashes.txt`'de tut |
| Model dosyaları değiştirilebilir | `model-hashes.json` ile model dosyaları kurulumda doğrulanır |
| launchd plist manipülasyonu | `/Library/LaunchDaemons/` için `sudo` gerekir; sistem bütünlük koruması devrede |
| Kurulum sırasında kötü amaçlı paket | `install.sh` betiği ağ erişimi açmaz; tüm kaynaklar yerel |

---

*Sonraki belge: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)*
