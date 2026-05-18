"""
Coordinator discovery via mDNS (Zeroconf).

The coordinator advertises itself as _transcription._tcp.local. on the LAN.
Workers browse for this service type to find the coordinator without manual
IP configuration.

Fallback chain:
  1. Environment variable / config (COORDINATOR_HOST set explicitly) → skip mDNS.
  2. mDNS browse for up to discovery_timeout_seconds.
  3. Cached coordinator URL from disk (from a previous successful discovery).
  4. Raise CoordinatorNotFoundError — caller should retry.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import datetime, timezone
from pathlib import Path

from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

from .config import WorkerConfig

logger = logging.getLogger(__name__)
UTC = timezone.utc

_CACHE_FILE = Path.home() / ".transcription-worker" / "coordinator-cache.json"
_CACHE_MAX_AGE_DAYS = 7


class CoordinatorNotFoundError(RuntimeError):
    """Raised when the coordinator cannot be located by any method."""


async def discover_coordinator(config: WorkerConfig) -> str:
    """
    Return the coordinator base URL (e.g. 'http://192.168.1.101:8080').

    1. If COORDINATOR_HOST is set in config, return it directly (no mDNS).
    2. Browse mDNS for up to config.mdns_discovery_timeout_seconds.
    3. Fall back to cached URL if recent enough.
    4. Raise CoordinatorNotFoundError.
    """
    # ── 1. Explicit configuration ─────────────────────────────────────────────
    if config.coordinator_base_url:
        logger.info(
            "Using configured coordinator URL",
            extra={"url": config.coordinator_base_url},
        )
        return config.coordinator_base_url

    # ── 2. mDNS discovery ─────────────────────────────────────────────────────
    logger.info(
        "Searching for coordinator via mDNS (%ds timeout)...",
        config.mdns_discovery_timeout_seconds,
    )
    url = await _browse_mdns(config)
    if url:
        _cache_url(url)
        logger.info("Coordinator discovered via mDNS", extra={"url": url})
        return url

    # ── 3. Cached URL ─────────────────────────────────────────────────────────
    cached = _load_cached_url()
    if cached:
        logger.warning(
            "mDNS discovery timed out; using cached coordinator URL",
            extra={"url": cached},
        )
        return cached

    # ── 4. Not found ──────────────────────────────────────────────────────────
    raise CoordinatorNotFoundError(
        "Could not locate coordinator via mDNS and no cached URL available. "
        "Set COORDINATOR_HOST in .env or ensure the coordinator is running on the LAN."
    )


async def _browse_mdns(config: WorkerConfig) -> str | None:
    """Browse for the coordinator service. Returns URL or None on timeout."""
    discovered: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    class _Listener:
        def add_service(self, zc: AsyncZeroconf, type_: str, name: str) -> None:
            if discovered.done():
                return
            info = zc.zeroconf.get_service_info(type_, name)
            if info and info.addresses:
                ip = socket.inet_ntoa(info.addresses[0])
                port = info.port
                url = f"http://{ip}:{port}"
                discovered.get_loop().call_soon_threadsafe(
                    discovered.set_result, url
                )

        def remove_service(self, *_: object) -> None:
            pass

        def update_service(self, *_: object) -> None:
            pass

    async with AsyncZeroconf() as azc:
        browser = AsyncServiceBrowser(
            azc.zeroconf, config.mdns_service_type, _Listener()
        )
        try:
            return await asyncio.wait_for(
                asyncio.shield(discovered),
                timeout=float(config.mdns_discovery_timeout_seconds),
            )
        except asyncio.TimeoutError:
            return None
        finally:
            await browser.async_cancel()


def _cache_url(url: str) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(
        json.dumps({"url": url, "cached_at": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )


def _load_cached_url() -> str | None:
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data["cached_at"])
        age_days = (datetime.now(UTC) - cached_at).days
        if age_days > _CACHE_MAX_AGE_DAYS:
            return None
        return data["url"]
    except Exception:
        return None
