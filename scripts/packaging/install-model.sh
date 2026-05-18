#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Model paketini güncelleme-güvenli yapıda kurar.
#
# /opt/transcription-models/
#   registry.json
#   current -> versions/<model-id>/<version>
#   versions/<model-id>/<version>/...
#
# Ortam:
#   MODEL_SRC      — kaynak paket dizini (payload/models/whisper-medium-mlx)
#   MODEL_ROOT     — hedef kök (/opt/transcription-models)
#   MODEL_ID       — model kimliği
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

MODEL_ROOT="${MODEL_ROOT:-/opt/transcription-models}"
MODEL_ID="${MODEL_ID:-whisper-medium-mlx}"
MODEL_SRC="${MODEL_SRC:?MODEL_SRC gerekli}"

if [ -f "${MODEL_SRC}/.skip" ]; then
    CURRENT="${MODEL_ROOT}/current"
    if [ -L "${CURRENT}" ] || [ -d "${CURRENT}" ]; then
        echo "  ℹ Model paketi atlandı — mevcut kurulum: ${CURRENT}"
        exit 0
    fi
    LEGACY="${MODEL_ROOT}/${MODEL_ID}"
    if [ -d "${LEGACY}" ]; then
        echo "  ℹ Eski düzen modeli kullanılıyor: ${LEGACY}"
        exit 0
    fi
    echo "HATA: Model paketi yok ve hedefte kurulum bulunamadı." >&2
    exit 1
fi

if [ ! -f "${MODEL_SRC}/weights.npz" ] && [ ! -f "${MODEL_SRC}/model.safetensors" ]; then
    echo "HATA: Geçersiz model kaynağı (weights.npz veya model.safetensors yok): ${MODEL_SRC}" >&2
    exit 1
fi

# Sürüm: MANIFEST.json veya varsayılan
MODEL_VERSION="1.0.0"
if [ -f "${MODEL_SRC}/MANIFEST.json" ]; then
    MODEL_VERSION="$(python3 -c "
import json
print(json.load(open('${MODEL_SRC}/MANIFEST.json')).get('version', '1.0.0'))
")"
fi

VERSION_DIR="${MODEL_ROOT}/versions/${MODEL_ID}/${MODEL_VERSION}"
CURRENT_LINK="${MODEL_ROOT}/current"
LEGACY_LINK="${MODEL_ROOT}/${MODEL_ID}"
REGISTRY="${MODEL_ROOT}/registry.json"

sudo mkdir -p "${MODEL_ROOT}/versions/${MODEL_ID}"
sudo mkdir -p "$(dirname "${VERSION_DIR}")"
sudo chown -R "$(whoami)":"$(id -gn)" "${MODEL_ROOT}"

if [ -d "${VERSION_DIR}" ]; then
    echo "  ℹ Sürüm zaten kurulu: ${VERSION_DIR}"
else
    echo "  Model kopyalanıyor -> ${VERSION_DIR}"
    mkdir -p "${VERSION_DIR}"
    rsync -a "${MODEL_SRC}/" "${VERSION_DIR}/"
    echo "  ✓ Model dosyaları kopyalandı"
fi

# Doğrulama
REPO_WORKER="$(cd "$(dirname "$0")/../.." && pwd)/worker"
INSTALLED_WORKER="${INSTALL_DIR:-}/worker"
PYTHONPATH=""
for candidate in "${INSTALLED_WORKER}" "${REPO_WORKER}"; do
    if [ -d "${candidate}/agent" ]; then
        PYTHONPATH="${candidate}"
        break
    fi
done

if [ -n "${PYTHONPATH}" ] && PYTHONPATH="${PYTHONPATH}" python3 -c "
from agent.model_store import validate_model_bundle
from pathlib import Path
validate_model_bundle(Path('${VERSION_DIR}'), strict_manifest=False)
print('OK')
" 2>/dev/null | grep -q OK; then
    echo "  ✓ Model paketi doğrulandı"
else
    for f in config.json weights.npz; do
        [ -f "${VERSION_DIR}/${f}" ] || { echo "HATA: Eksik ${f}"; exit 1; }
    done
    echo "  ✓ Gerekli dosyalar mevcut"
fi

# Atomik 'current' symlink güncelleme
TMP_LINK="${MODEL_ROOT}/.current.tmp.$$"
ln -sfn "versions/${MODEL_ID}/${MODEL_VERSION}" "${TMP_LINK}"
mv -f "${TMP_LINK}" "${CURRENT_LINK}"

# Geriye uyumluluk: düz whisper-medium-mlx symlink
if [ ! -e "${LEGACY_LINK}" ] || [ -L "${LEGACY_LINK}" ]; then
    ln -sfn "current" "${LEGACY_LINK}"
fi

# registry.json
INSTALLED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
python3 - <<PY
import json
from pathlib import Path

registry_path = Path("${REGISTRY}")
data = {"schema_version": 1, "current": None, "installed": []}
if registry_path.is_file():
    data = json.loads(registry_path.read_text())

current = {
    "model_id": "${MODEL_ID}",
    "version": "${MODEL_VERSION}",
    "path": "versions/${MODEL_ID}/${MODEL_VERSION}",
}
data["current"] = current

installed = data.get("installed") or []
installed = [e for e in installed if not (
    e.get("model_id") == "${MODEL_ID}" and e.get("version") == "${MODEL_VERSION}"
)]
installed.append({
    "model_id": "${MODEL_ID}",
    "version": "${MODEL_VERSION}",
    "path": current["path"],
    "installed_at": "${INSTALLED_AT}",
})
data["installed"] = installed
registry_path.write_text(json.dumps(data, indent=2) + "\n")
PY

echo "  ✓ Aktif model: ${CURRENT_LINK} -> versions/${MODEL_ID}/${MODEL_VERSION}"
echo "  ✓ registry.json güncellendi"
