#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Whisper MLX modelini yerel paket dizinine indirir ve MANIFEST.json oluşturur.
#
# Kullanım:
#   ./scripts/packaging/bundle-model.sh
#   ./scripts/packaging/bundle-model.sh --model-id whisper-medium-mlx
#   ./scripts/packaging/bundle-model.sh --verify-only
#
# Çıktı: worker/packaging/models/<model-id>/ (+ MANIFEST.json)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/lib/python311.sh"
MODEL_ID="${MODEL_ID:-whisper-medium-mlx}"
MODEL_VERSION="${MODEL_VERSION:-1.0.0}"
VERIFY_ONLY=false

for arg in "$@"; do
    case "${arg}" in
        --model-id=*) MODEL_ID="${arg#*=}" ;;
        --version=*) MODEL_VERSION="${arg#*=}" ;;
        --verify-only) VERIFY_ONLY=true ;;
        -h|--help)
            echo "Kullanım: $0 [--model-id=ID] [--version=VER] [--verify-only]"
            exit 0
            ;;
    esac
done

CATALOG="${REPO_ROOT}/worker/packaging/models/MODEL_CATALOG.json"
MODEL_DIR="${REPO_ROOT}/worker/packaging/models/${MODEL_ID}"

if [ ! -f "${CATALOG}" ]; then
    echo "HATA: MODEL_CATALOG.json bulunamadı"
    exit 1
fi

SOURCE_REPO="$(python3 -c "
import json, sys
cat = json.load(open('${CATALOG}'))
print(cat['models']['${MODEL_ID}']['source_repo'])
" 2>/dev/null || echo "mlx-community/whisper-medium-mlx")"

if [ "${VERIFY_ONLY}" = true ]; then
    exec python3 "${REPO_ROOT}/scripts/packaging/write_model_manifest.py" \
        --bundle-dir "${MODEL_DIR}" \
        --model-id "${MODEL_ID}" \
        --version "${MODEL_VERSION}" \
        --source-repo "${SOURCE_REPO}" \
        --verify-only
fi

resolve_build_python311

mkdir -p "${MODEL_DIR}"

if [ -f "${MODEL_DIR}/weights.npz" ] && [ -f "${MODEL_DIR}/MANIFEST.json" ]; then
    echo "Model zaten mevcut: ${MODEL_DIR}"
else
    echo "Model indiriliyor: ${SOURCE_REPO} -> ${MODEL_DIR}"
    BUILD_VENV="$(mktemp -d "${TMPDIR:-/tmp}/bundle-model-venv.XXXXXX")"
    "${PYTHON}" -m venv "${BUILD_VENV}"
    # shellcheck disable=SC1091
    source "${BUILD_VENV}/bin/activate"
    pip install -q huggingface_hub
    MODEL_DIR="${MODEL_DIR}" SOURCE_REPO="${SOURCE_REPO}" python - <<'PY'
import os
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id=os.environ["SOURCE_REPO"],
    local_dir=os.environ["MODEL_DIR"],
    ignore_patterns=["*.msgpack", "flax_model*"],
)
print("İndirme tamamlandı.")
PY
    deactivate
    rm -rf "${BUILD_VENV}"
fi

REQUIRED_FILES="$(python3 -c "
import json
cat = json.load(open('${CATALOG}'))
print(','.join(cat['models']['${MODEL_ID}']['required_files']))
")"
python3 "${REPO_ROOT}/scripts/packaging/write_model_manifest.py" \
    --bundle-dir "${MODEL_DIR}" \
    --model-id "${MODEL_ID}" \
    --version "${MODEL_VERSION}" \
    --source-repo "${SOURCE_REPO}" \
    --required-files "${REQUIRED_FILES}"

echo ""
echo "Model paketi hazır: ${MODEL_DIR}"
du -sh "${MODEL_DIR}" | awk '{print "Boyut:", $1}'
