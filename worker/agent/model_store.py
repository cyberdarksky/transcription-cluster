"""
Local model bundle storage and validation.

Offline policy:
  - Workers MUST load models from an absolute local directory path.
  - HuggingFace repo IDs (e.g. mlx-community/whisper-medium-mlx) are rejected
    at startup to prevent accidental network downloads in production.

Layout (installed):
  /opt/transcription-models/
    registry.json
    current -> versions/whisper-medium-mlx/1.0.0
    versions/
      whisper-medium-mlx/
        1.0.0/
          MANIFEST.json
          config.json
          model.safetensors
          tokenizer.json
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ROOT = Path("/opt/transcription-models")
DEFAULT_MODEL_ID = "whisper-medium-mlx"
DEFAULT_MODEL_VERSION = "1.0.0"
CURRENT_LINK_NAME = "current"
MANIFEST_FILENAME = "MANIFEST.json"
REGISTRY_FILENAME = "registry.json"

# HuggingFace-style repo id: "org/name" without leading slash or drive letter.
_HF_REPO_PARTS = 2


class ModelBundleError(Exception):
    """Raised when a local model bundle is missing or invalid."""


@dataclass(frozen=True)
class ModelBundle:
    model_id: str
    version: str
    path: Path
    backend: str
    source_repo: str | None = None

    @property
    def resolved_path(self) -> Path:
        return self.path.resolve()


def is_remote_model_reference(value: str | Path) -> bool:
    """
    Return True if value looks like a HuggingFace repo id, not a local path.

    Examples rejected: mlx-community/whisper-medium-mlx
    Examples accepted: /opt/transcription-models/current
    """
    text = str(value).strip()
    if not text:
        return False
    path = Path(text)
    if path.is_absolute():
        return False
    # Relative paths with .. or . are treated as local
    if text.startswith((".", "~")):
        return False
    parts = text.split("/")
    return len(parts) == _HF_REPO_PARTS and all(parts) and ".." not in parts


def resolve_model_path(model_path: Path, *, model_root: Path = DEFAULT_MODEL_ROOT) -> Path:
    """Expand user home and resolve symlinks."""
    expanded = model_path.expanduser()
    if not expanded.is_absolute():
        # Relative paths are resolved against model root
        expanded = (model_root / expanded).resolve()
    else:
        expanded = expanded.resolve()
    return expanded


def load_manifest(bundle_dir: Path) -> dict[str, Any]:
    manifest_path = bundle_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ModelBundleError(f"MANIFEST.json missing: {bundle_dir}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ModelBundleError(f"Invalid MANIFEST.json: {manifest_path}") from exc
    if not isinstance(data, dict):
        raise ModelBundleError(f"MANIFEST.json must be a JSON object: {manifest_path}")
    return data


def _md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_manifest_files(bundle_dir: Path, manifest: dict[str, Any]) -> None:
    files_section = manifest.get("files")
    if not isinstance(files_section, dict):
        return
    for name, meta in files_section.items():
        file_path = bundle_dir / name
        if not file_path.is_file():
            raise ModelBundleError(f"Manifest lists missing file: {name}")
        if not isinstance(meta, dict):
            continue
        expected_md5 = meta.get("md5")
        if expected_md5 and _md5_file(file_path) != expected_md5:
            raise ModelBundleError(
                f"Checksum mismatch for {name} (expected {expected_md5})"
            )
        expected_size = meta.get("size_bytes")
        if expected_size is not None and file_path.stat().st_size != int(expected_size):
            raise ModelBundleError(f"Size mismatch for {name}")


def validate_model_bundle(
    model_path: Path,
    *,
    strict_manifest: bool = True,
    model_id: str | None = None,
) -> ModelBundle:
    """
    Validate a local model directory for offline inference.

    Raises ModelBundleError if the bundle cannot be used.
    """
    if is_remote_model_reference(model_path):
        raise ModelBundleError(
            f"MODEL_PATH must be a local directory, not a HuggingFace repo id: {model_path!s}. "
            "Run install-worker.sh or set MODEL_PATH=/opt/transcription-models/current"
        )

    bundle_dir = resolve_model_path(model_path)

    if not bundle_dir.exists():
        raise ModelBundleError(f"Model directory not found: {bundle_dir}")
    if not bundle_dir.is_dir():
        raise ModelBundleError(f"Model path is not a directory: {bundle_dir}")

    manifest: dict[str, Any] = {}
    manifest_path = bundle_dir / MANIFEST_FILENAME
    if manifest_path.is_file():
        manifest = load_manifest(bundle_dir)
    elif strict_manifest:
        raise ModelBundleError(
            f"{MANIFEST_FILENAME} required for offline bundles: {bundle_dir}"
        )

    resolved_id = str(manifest.get("model_id") or model_id or DEFAULT_MODEL_ID)
    resolved_version = str(manifest.get("version") or DEFAULT_MODEL_VERSION)
    backend = str(manifest.get("backend") or "mlx")
    source_repo = manifest.get("source_repo")

    required_files: list[str] = list(
        manifest.get("required_files")
        or ["config.json", "weights.npz"]
    )
    for filename in required_files:
        if not (bundle_dir / filename).is_file():
            raise ModelBundleError(f"Required model file missing: {filename} in {bundle_dir}")

    if manifest:
        _verify_manifest_files(bundle_dir, manifest)

    bundle = ModelBundle(
        model_id=resolved_id,
        version=resolved_version,
        path=bundle_dir,
        backend=backend,
        source_repo=str(source_repo) if source_repo else None,
    )
    logger.info(
        "Model bundle validated",
        extra={
            "model_id": bundle.model_id,
            "version": bundle.version,
            "path": str(bundle.resolved_path),
            "backend": bundle.backend,
        },
    )
    return bundle


def read_registry(model_root: Path = DEFAULT_MODEL_ROOT) -> dict[str, Any]:
    registry_path = model_root / REGISTRY_FILENAME
    if not registry_path.is_file():
        return {"schema_version": 1, "current": None, "installed": []}
    return json.loads(registry_path.read_text(encoding="utf-8"))


def current_bundle_path(model_root: Path = DEFAULT_MODEL_ROOT) -> Path | None:
    """Return resolved path of the 'current' model symlink if valid."""
    current = model_root / CURRENT_LINK_NAME
    if current.is_symlink() or current.exists():
        try:
            resolved = current.resolve()
            if resolved.is_dir():
                return resolved
        except OSError:
            return None
    registry = read_registry(model_root)
    current_info = registry.get("current")
    if isinstance(current_info, dict):
        rel = current_info.get("path")
        if rel:
            candidate = (model_root / str(rel)).resolve()
            if candidate.is_dir():
                return candidate
    return None


def write_manifest(
    bundle_dir: Path,
    *,
    model_id: str,
    version: str,
    backend: str = "mlx",
    source_repo: str | None = None,
    required_files: list[str] | None = None,
) -> Path:
    """Write MANIFEST.json with file checksums (used at bundle build time)."""
    required = required_files or ["config.json", "weights.npz"]
    files_meta: dict[str, Any] = {}
    for name in required:
        file_path = bundle_dir / name
        if not file_path.is_file():
            raise ModelBundleError(f"Cannot write manifest — missing file: {name}")
        stat = file_path.stat()
        files_meta[name] = {
            "size_bytes": stat.st_size,
            "md5": _md5_file(file_path),
        }

    manifest = {
        "schema_version": 1,
        "model_id": model_id,
        "version": version,
        "backend": backend,
        "source_repo": source_repo,
        "required_files": required,
        "files": files_meta,
        "bundled_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = bundle_dir / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path
