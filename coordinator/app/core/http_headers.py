"""HTTP header helpers (ASCII-safe for Starlette latin-1 encoding)."""
from __future__ import annotations

from urllib.parse import quote


def content_disposition_attachment(filename: str) -> str:
    """
    RFC 5987 Content-Disposition safe for Unicode filenames (Turkish, etc.).

    Starlette encodes header values as latin-1; raw İ/ş in filename= breaks downloads.
    """
    ascii_fallback = "".join(
        c if ord(c) < 128 and c not in ('"', "\\") else "_"
        for c in filename
    ).strip() or "download.mp3"
    encoded = quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'
