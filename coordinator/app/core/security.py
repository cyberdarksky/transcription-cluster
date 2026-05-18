from __future__ import annotations

from pathlib import Path

from .exceptions import PathTraversalError


def safe_join(base: Path, user_path: str) -> Path:
    """
    Safely join a user-supplied path to a base directory.
    Raises PathTraversalError if the resolved path escapes the base.
    """
    resolved = (base / user_path).resolve()
    base_resolved = base.resolve()

    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise PathTraversalError(user_path)

    return resolved


def sanitize_relative_path(path: str) -> str:
    """
    Strip leading slashes and remove dangerous path components.
    Returns a safe relative path string.
    """
    cleaned = path.lstrip("/").replace("../", "").replace("..\\", "")
    parts = [p for p in Path(cleaned).parts if p not in (".", "..")]
    return str(Path(*parts)) if parts else ""
