#!/usr/bin/env python3
"""Write or verify MANIFEST.json for a local model bundle."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo without installing the worker package
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "worker") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "worker"))

from agent.model_store import (  # noqa: E402
    ModelBundleError,
    validate_model_bundle,
    write_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Write or verify model MANIFEST.json")
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--model-id", default="whisper-medium-mlx")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--source-repo", default="mlx-community/whisper-medium-mlx")
    parser.add_argument("--backend", default="mlx")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    bundle_dir = args.bundle_dir.resolve()
    if not bundle_dir.is_dir():
        print(f"HATA: Dizin yok: {bundle_dir}", file=sys.stderr)
        return 1

    if args.verify_only:
        try:
            bundle = validate_model_bundle(bundle_dir, strict_manifest=False)
        except ModelBundleError as exc:
            print(f"HATA: {exc}", file=sys.stderr)
            return 1
        print(f"  ✓ Geçerli paket: {bundle.model_id} v{bundle.version}")
        return 0

    try:
        path = write_manifest(
            bundle_dir,
            model_id=args.model_id,
            version=args.version,
            backend=args.backend,
            source_repo=args.source_repo,
        )
        bundle = validate_model_bundle(bundle_dir, strict_manifest=True)
    except ModelBundleError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1

    print(f"  ✓ MANIFEST yazıldı: {path}")
    print(f"  ✓ Doğrulandı: {bundle.model_id} v{bundle.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
