#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_dev.sh — Development environment setup
# Run this once after cloning the repository.
# Requires: Python 3.11, PostgreSQL running locally
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=== Transkripsiyon Kümesi — Geliştirme Ortamı Kurulumu ==="
echo ""

# ── 1. Python version check ───────────────────────────────────────────────────
echo "[1/6] Python sürümü kontrol ediliyor..."
if ! command -v python3.11 &>/dev/null && ! python3 --version 2>&1 | grep -q "3\.11"; then
    echo "  UYARI: Python 3.11 gerekli. Mevcut: $(python3 --version 2>&1)"
    echo "  Homebrew ile kurulum: brew install python@3.11"
    echo "  veya https://python.org/downloads/ adresinden indirin"
    exit 1
fi
PYTHON=$(command -v python3.11 || command -v python3)
echo "  ✓ $($PYTHON --version)"

# ── 2. Virtual environment ────────────────────────────────────────────────────
echo "[2/6] Sanal ortam oluşturuluyor..."
if [ ! -d ".venv" ]; then
    $PYTHON -m venv .venv
    echo "  ✓ .venv oluşturuldu"
else
    echo "  ℹ .venv zaten mevcut"
fi

# Activate
source .venv/bin/activate
pip install --quiet --upgrade pip

# ── 3. Dependencies ────────────────────────────────────────────────────────────
echo "[3/6] Bağımlılıklar yükleniyor..."
pip install --quiet -r requirements-dev.txt
echo "  ✓ Bağımlılıklar yüklendi"

# ── 4. Environment file ────────────────────────────────────────────────────────
echo "[4/6] Ortam değişkenleri yapılandırılıyor..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "  ✓ .env dosyası oluşturuldu (.env.example'dan)"
    echo "  → Gerekirse .env dosyasını düzenleyin"
else
    echo "  ℹ .env zaten mevcut"
fi

# ── 5. PostgreSQL database ────────────────────────────────────────────────────
echo "[5/6] Veritabanı oluşturuluyor..."
if command -v createdb &>/dev/null; then
    createdb transcription_cluster 2>/dev/null && \
        echo "  ✓ transcription_cluster veritabanı oluşturuldu" || \
        echo "  ℹ transcription_cluster zaten mevcut"
else
    echo "  UYARI: createdb bulunamadı. PostgreSQL kurulu ve PATH'te olduğundan emin olun."
    echo "  Postgres.app kullanıyorsanız: export PATH=\"/Applications/Postgres.app/Contents/Versions/latest/bin:\$PATH\""
fi

# ── 6. Run migrations ─────────────────────────────────────────────────────────
echo "[6/6] Migrasyon çalıştırılıyor..."
if alembic upgrade head 2>/dev/null; then
    echo "  ✓ Veritabanı şeması güncel"
else
    echo "  HATA: Migrasyon başarısız!"
    echo "  PostgreSQL çalışıyor mu? DATABASE_URL doğru mu?"
    echo "  .env dosyasında DATABASE_URL'yi kontrol edin"
    exit 1
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=== Kurulum tamamlandı! ==="
echo ""
echo "Başlatmak için:"
echo "  source .venv/bin/activate"
echo "  ./scripts/start.sh"
echo ""
echo "veya doğrudan:"
echo "  uvicorn app.main:app --reload --port 8080"
echo ""
echo "API dokümantasyonu: http://localhost:8080/docs"
