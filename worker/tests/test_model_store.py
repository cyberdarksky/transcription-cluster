"""Tests for offline model bundle validation."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agent.model_store import (
    ModelBundleError,
    is_remote_model_reference,
    validate_model_bundle,
    write_manifest,
)


def test_rejects_hf_repo_id() -> None:
    assert is_remote_model_reference("mlx-community/whisper-medium-mlx")
    assert not is_remote_model_reference("/opt/transcription-models/current")


def test_validate_minimal_bundle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("config.json", "model.safetensors", "tokenizer.json"):
            (root / name).write_bytes(b"x")
        write_manifest(root, model_id="whisper-medium-mlx", version="1.0.0")
        bundle = validate_model_bundle(root)
        assert bundle.model_id == "whisper-medium-mlx"
        assert bundle.version == "1.0.0"


def test_validate_rejects_remote_reference() -> None:
    with pytest.raises(ModelBundleError, match="HuggingFace"):
        validate_model_bundle(Path("mlx-community/whisper-medium-mlx"))


def test_manifest_checksum_mismatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config.json").write_text("{}")
        (root / "model.safetensors").write_bytes(b"data")
        (root / "tokenizer.json").write_text("{}")
        write_manifest(root, model_id="whisper-medium-mlx", version="1.0.0")
        (root / "model.safetensors").write_bytes(b"corrupt")
        with pytest.raises(ModelBundleError, match="Checksum"):
            validate_model_bundle(root)
