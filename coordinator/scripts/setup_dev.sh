#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_dev.sh — Development environment setup
# Run this once after cloning the repository.
# Requires: Python 3.11.x, PostgreSQL running locally
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$PROJECT_DIR")"

# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/lib/python311.sh"

cd "$PROJECT_DIR"

echo "=== Transkripsiyon Kümesi — Geliştirme Ortamı Kurulumu ==="
echo ""

# ── 1. Python version check ───────────────────────────────────────────────────
echo "[1/6] Python sürümü kontrol ediliyor..."
require_python311 ""
echo "  ✓ $($PYTHON --version)"

# ── 2. Virtual environment ────────────────────────────────────────────────────
echo "[2/6] Sanal ortam oluşturuluyor..."
if [ ! -d ".venv" ]; then
    "${PYTHON}" -m venv .venv
    echo "  ✓ .venv oluşturuldu"
else
    assert_venv_python311 "${PROJECT_DIR}/.venv/bin/python3" || {
        echo "  ℹ Mevcut .venv Python 3.11 değil — yeniden oluşturuluyor..."
        rm -rf .venv
        "${PYTHON}" -m venv .venv
    }
    echo "  ✓ .venv hazır"
fi

# Activate
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip

# ── 3. Dependencies ────────────────────────────────────────────────────────────
echo "[3/6] Bağımlılıklar yükleniyor..."
pip install -q -r requirements-dev.txt
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
