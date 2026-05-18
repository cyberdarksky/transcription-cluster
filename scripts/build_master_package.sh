#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Master (koordinatör) kurulum paketini oluşturur.
#
# Kullanım:
#   ./scripts/build_master_package.sh
#   ./scripts/build_master_package.sh --skip-dashboard
#
# Ön koşullar (paket hazırlayan makinede, bir kez):
#   - coordinator/packaging/postgres/Postgres.app  (postgresapp.com)
#   - coordinator/packaging/python/python-3.12*.pkg (opsiyonel)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE_DIR="${REPO_ROOT}/dist/master-package"
VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
SKIP_DASHBOARD=false
TARGET_PY_VER="3.12"

for arg in "$@"; do
    case "${arg}" in
        --skip-dashboard) SKIP_DASHBOARD=true ;;
        -h|--help)
            echo "Kullanım: $0 [--skip-dashboard]"
            exit 0
            ;;
        *)
            echo "Bilinmeyen argüman: ${arg}"
            exit 1
            ;;
    esac
done

if [ "$(uname -m)" != "arm64" ]; then
    echo "HATA: Paket arm64 için derlenmelidir."
    exit 1
fi

PYTHON=""
for candidate in python3.12 python3.11; do
    if command -v "${candidate}" &>/dev/null; then
        PYTHON="${candidate}"
        break
    fi
done
[ -n "${PYTHON}" ] || { echo "HATA: python3.12 veya python3.11 gerekli."; exit 1; }

echo "=== Master Paketi Hazırlanıyor (v${VERSION}) ==="
echo ""

rm -rf "${PACKAGE_DIR}"
mkdir -p "${PACKAGE_DIR}/payload"/{wheelhouse,postgres,python,launchd,scripts,dashboard}

# ── 1. Ön koşullar ────────────────────────────────────────────────────────────
echo "[1/6] Ön koşul dosyaları kontrol ediliyor..."
POSTGRES_SRC="${REPO_ROOT}/coordinator/packaging/postgres/Postgres.app"
if [ -d "${POSTGRES_SRC}" ]; then
    echo "  Postgres.app kopyalanıyor..."
    cp -R "${POSTGRES_SRC}" "${PACKAGE_DIR}/payload/postgres/"
    "${POSTGRES_SRC}/Contents/Versions/latest/bin/postgres" --version \
        > "${PACKAGE_DIR}/payload/postgres/postgres-version.txt" 2>/dev/null \
        || echo "Postgres.app (bundled)" > "${PACKAGE_DIR}/payload/postgres/postgres-version.txt"
    echo "  ✓ Postgres.app"
else
    echo "  UYARI: ${POSTGRES_SRC} eksik — paket postgres olmadan oluşturuluyor"
    echo "  Kurulum sırasında mevcut Postgres.app kullanılmalıdır"
fi

if compgen -G "${REPO_ROOT}/coordinator/packaging/python/python-3.12*.pkg" > /dev/null; then
    cp "${REPO_ROOT}"/coordinator/packaging/python/python-3.12*.pkg "${PACKAGE_DIR}/payload/python/"
    echo "  ✓ Python .pkg eklendi"
fi

# ── 2. Wheelhouse ─────────────────────────────────────────────────────────────
echo "[2/6] Python wheel'ları indiriliyor..."
BUILD_VENV="$(mktemp -d "${TMPDIR:-/tmp}/master-build-venv.XXXXXX")"
"${PYTHON}" -m venv "${BUILD_VENV}"
# shellcheck disable=SC1091
source "${BUILD_VENV}/bin/activate"
pip install -q pip wheel

if ! pip download \
    --platform macosx_14_0_arm64 \
    --python-version "${TARGET_PY_VER}" \
    --only-binary=:all: \
    --dest "${PACKAGE_DIR}/payload/wheelhouse" \
    -r "${REPO_ROOT}/coordinator/requirements.txt"; then
    pip download \
        --platform macosx_14_0_arm64 \
        --python-version "${TARGET_PY_VER}" \
        --dest "${PACKAGE_DIR}/payload/wheelhouse" \
        -r "${REPO_ROOT}/coordinator/requirements.txt"
fi
deactivate
rm -rf "${BUILD_VENV}"
echo "  ✓ $(find "${PACKAGE_DIR}/payload/wheelhouse" -name '*.whl' | wc -l | tr -d ' ') wheel"

# ── 3. Dashboard ──────────────────────────────────────────────────────────────
echo "[3/6] Dashboard..."
if [ "${SKIP_DASHBOARD}" = true ]; then
    echo "  ℹ --skip-dashboard: atlanıyor"
elif [ -d "${REPO_ROOT}/dashboard/dist" ]; then
    cp -R "${REPO_ROOT}/dashboard/dist/." "${PACKAGE_DIR}/payload/dashboard/dist/"
    echo "  ✓ Mevcut dashboard/dist kopyalandı"
elif [ -d "${REPO_ROOT}/dashboard" ] && command -v npm &>/dev/null; then
    echo "  Dashboard derleniyor..."
    (cd "${REPO_ROOT}/dashboard" && npm ci --silent && npm run build --silent)
    cp -R "${REPO_ROOT}/dashboard/dist/." "${PACKAGE_DIR}/payload/dashboard/dist/"
    echo "  ✓ Dashboard derlendi"
else
    echo "  UYARI: dashboard/dist yok — npm run build ile oluşturun"
    mkdir -p "${PACKAGE_DIR}/payload/dashboard/dist"
fi

# ── 4. Koordinatör kaynağı ───────────────────────────────────────────────────
echo "[4/6] Koordinatör kodu kopyalanıyor..."
rsync -a \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='tests' \
    --exclude='packaging/postgres' \
    "${REPO_ROOT}/coordinator/" "${PACKAGE_DIR}/payload/coordinator/"
chmod +x "${PACKAGE_DIR}/payload/coordinator/coordinator.sh"
chmod +x "${PACKAGE_DIR}/payload/coordinator/scripts/wait-postgres.sh"

# ── 5. Kurulum betikleri ──────────────────────────────────────────────────────
echo "[5/6] Kurulum dosyaları ekleniyor..."
cp "${REPO_ROOT}/scripts/packaging/init-postgres.sh" \
    "${REPO_ROOT}/scripts/packaging/bootstrap-master-config.sh" \
    "${REPO_ROOT}/scripts/packaging/ensure-postgres.sh" \
    "${PACKAGE_DIR}/payload/scripts/"
chmod +x "${PACKAGE_DIR}/payload/scripts/"*.sh

cp "${REPO_ROOT}/scripts/install/master/install-master.sh" "${PACKAGE_DIR}/"
cp "${REPO_ROOT}/scripts/install/master/uninstall-master.sh" "${PACKAGE_DIR}/"
chmod +x "${PACKAGE_DIR}/install-master.sh" "${PACKAGE_DIR}/uninstall-master.sh"

cp "${REPO_ROOT}/scripts/launchd/master/"*.plist "${PACKAGE_DIR}/payload/launchd/"
cp "${REPO_ROOT}/scripts/packaging/verify-master-install.sh" "${PACKAGE_DIR}/"
chmod +x "${PACKAGE_DIR}/verify-master-install.sh"

cat > "${PACKAGE_DIR}/MANIFEST.txt" << EOF
Transkripsiyon Master Paketi v${VERSION}
Platform: macOS 14+ arm64

  install-master.sh   — Kurulum
  uninstall-master.sh — Kaldırma
  verify-master-install.sh
  payload/coordinator/  — FastAPI uygulaması
  payload/dashboard/  — React derlemesi
  payload/wheelhouse/   — Python bağımlılıkları
  payload/postgres/     — Postgres.app
  payload/scripts/      — init-postgres, bootstrap-config
EOF

# ── 6. Arşiv ──────────────────────────────────────────────────────────────────
echo "[6/6] Arşiv oluşturuluyor..."
mkdir -p "${REPO_ROOT}/dist"
ARCHIVE="${REPO_ROOT}/dist/master-v${VERSION}-arm64.tar.gz"
tar -czf "${ARCHIVE}" -C "${REPO_ROOT}/dist" master-package/

echo ""
echo "=== Paket Hazır ==="
echo "Dizin : ${PACKAGE_DIR}"
echo "Arşiv : ${ARCHIVE}"
echo "Boyut : $(du -sh "${ARCHIVE}" | cut -f1)"
