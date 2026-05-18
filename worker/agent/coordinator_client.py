"""
CoordinatorClient — all HTTP communication with the coordinator.

Design:
- Built on httpx AsyncClient.
- Request timeouts are set per-operation (download has long read timeout;
  registration/heartbeat have short timeouts).
- Reconnect logic lives in the ReconnectStrategy helper used by the job loop.
- All methods raise httpx.HTTPStatusError or httpx.RequestError on failure;
  callers decide whether to retry or propagate.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiofiles
import httpx

logger = logging.getLogger(__name__)

_SHORT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=10.0, pool=5.0)
_UPLOAD_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=300.0, pool=5.0)


@dataclass
class JobAssignment:
    job_id: uuid.UUID
    input_path: str
    original_filename: str
    relative_folder: str
    file_size_bytes: int | None
    download_url: str
    whisper_model: str
    whisper_language: str
    whisper_word_timestamps: bool
    max_job_duration_seconds: int | None


@dataclass
class RegisterResponse:
    worker_id: uuid.UUID
    heartbeat_interval_seconds: int
    websocket_url: str
    recovery_grace_active: bool
    cancel_current_job: bool
    whisper_model: str
    whisper_language: str
    whisper_word_timestamps: bool
    job_timeout_multiplier: int


class CoordinatorClient:
    """
    Async HTTP client for all coordinator interactions.
    Instantiate once and reuse across tasks (httpx AsyncClient is thread/task-safe).
    """

    def __init__(self, base_url: str, worker_version: str = "1.0.0") -> None:
        self.base_url = base_url.rstrip("/")
        self._worker_version = worker_version
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=_SHORT_TIMEOUT,
            limits=httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
            ),
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── Registration ──────────────────────────────────────────────────────────

    async def register(
        self,
        stable_worker_id: uuid.UUID,
        mac_address: str,
        hostname: str,
        ip_address: str,
        cpu_model: str | None,
        cpu_cores: int | None,
        memory_total_gb: float | None,
        gpu_model: str | None,
        current_job_id: uuid.UUID | None = None,
        current_job_status: str | None = None,
    ) -> RegisterResponse:
        payload: dict[str, Any] = {
            "stable_worker_id": str(stable_worker_id),
            "mac_address": mac_address,
            "hostname": hostname,
            "ip_address": ip_address,
            "whisper_backend": "mlx-whisper",
            "worker_version": self._worker_version,
        }
        if cpu_model:
            payload["cpu_model"] = cpu_model
        if cpu_cores:
            payload["cpu_cores"] = cpu_cores
        if memory_total_gb:
            payload["memory_total_gb"] = memory_total_gb
        if gpu_model:
            payload["gpu_model"] = gpu_model
        if current_job_id:
            payload["current_job_id"] = str(current_job_id)
        if current_job_status:
            payload["current_job_status"] = current_job_status

        resp = await self._http.post("/api/v1/worker/register", json=payload)
        resp.raise_for_status()
        data = resp.json()

        settings = data.get("settings", {})
        return RegisterResponse(
            worker_id=uuid.UUID(data["worker_id"]),
            heartbeat_interval_seconds=data.get("heartbeat_interval_seconds", 30),
            websocket_url=data["websocket_url"],
            recovery_grace_active=data.get("recovery_grace_active", False),
            cancel_current_job=data.get("cancel_current_job", False),
            whisper_model=settings.get("whisper_model", "/opt/transcription-models/current"),
            whisper_language=settings.get("whisper_language", "tr"),
            whisper_word_timestamps=settings.get("whisper_word_timestamps", True),
            job_timeout_multiplier=settings.get("job_timeout_multiplier", 5),
        )

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    async def heartbeat(
        self,
        worker_id: uuid.UUID,
        status: str,
        current_job_id: uuid.UUID | None,
        job_progress_percent: float | None,
        metrics: dict[str, Any],
    ) -> list[dict]:
        """Send heartbeat. Returns list of pending commands from coordinator."""
        payload = {
            "worker_id": str(worker_id),
            "status": status,
            "current_job_id": str(current_job_id) if current_job_id else None,
            "job_progress_percent": job_progress_percent,
            "metrics": metrics,
        }
        resp = await self._http.post("/api/v1/worker/heartbeat", json=payload)
        resp.raise_for_status()
        return resp.json().get("pending_commands", [])

    # ── Job claiming ──────────────────────────────────────────────────────────

    async def claim_next_job(self, worker_id: uuid.UUID) -> JobAssignment | None:
        """
        Try to claim the next available job.
        Returns None if the queue is empty (coordinator returns 204).
        """
        resp = await self._http.get(
            "/api/v1/worker/jobs/next",
            params={"worker_id": str(worker_id)},
        )
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        data = resp.json()
        ws = data.get("whisper_settings", {})
        return JobAssignment(
            job_id=uuid.UUID(data["job_id"]),
            input_path=data["input_path"],
            original_filename=data["original_filename"],
            relative_folder=data.get("relative_folder", ""),
            file_size_bytes=data.get("file_size_bytes"),
            download_url=data["download_url"],
            whisper_model=ws.get("model", "/opt/transcription-models/current"),
            whisper_language=ws.get("language", "tr"),
            whisper_word_timestamps=ws.get("word_timestamps", True),
            max_job_duration_seconds=data.get("max_job_duration_seconds"),
        )

    # ── State advancement ─────────────────────────────────────────────────────

    async def advance_state(
        self, job_id: uuid.UUID, worker_id: uuid.UUID, new_state: str
    ) -> None:
        resp = await self._http.post(
            f"/api/v1/worker/jobs/{job_id}/state",
            params={"worker_id": str(worker_id), "new_state": new_state},
        )
        resp.raise_for_status()

    async def report_start(self, job_id: uuid.UUID, worker_id: uuid.UUID) -> None:
        resp = await self._http.post(
            f"/api/v1/worker/jobs/{job_id}/start",
            json={"worker_id": str(worker_id)},
        )
        resp.raise_for_status()

    async def report_progress(
        self,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
        percent: float,
        elapsed_seconds: float | None = None,
    ) -> list[dict]:
        """Report progress. Returns pending commands (PAUSE / CANCEL) if any."""
        payload: dict[str, Any] = {
            "worker_id": str(worker_id),
            "percent": round(percent, 1),
        }
        if elapsed_seconds is not None:
            payload["elapsed_seconds"] = round(elapsed_seconds, 1)

        resp = await self._http.post(
            f"/api/v1/worker/jobs/{job_id}/progress",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        cmd = data.get("command")
        return [{"command": cmd}] if cmd else []

    # ── Download ──────────────────────────────────────────────────────────────

    async def download_file(
        self,
        download_url: str,
        dest_path: Path,
        chunk_size: int = 1_048_576,
    ) -> int:
        """
        Stream the MP3 file to dest_path.
        Supports HTTP Range for resumable downloads:
        if dest_path already exists (partial download), sends Range header.

        Returns total bytes written.
        """
        headers = {}
        existing_size = 0
        if dest_path.exists():
            existing_size = dest_path.stat().st_size
            if existing_size > 0:
                headers["Range"] = f"bytes={existing_size}-"
                logger.debug(
                    "Resuming download at byte %d", existing_size,
                    extra={"dest": str(dest_path)},
                )

        url = download_url if download_url.startswith("http") else self.base_url + download_url

        async with self._http.stream("GET", url, headers=headers, timeout=_DOWNLOAD_TIMEOUT) as resp:
            if resp.status_code == 416:
                # Range not satisfiable → file already complete
                return existing_size
            resp.raise_for_status()

            mode = "ab" if resp.status_code == 206 else "wb"
            written = existing_size if mode == "ab" else 0

            # BUG-FIX: Use aiofiles for async chunk writes.
            # Synchronous f.write() in an async loop blocks the event loop
            # for the duration of each disk write, preventing heartbeat
            # and WebSocket tasks from running during large downloads.
            async with aiofiles.open(dest_path, mode) as f:
                async for chunk in resp.aiter_bytes(chunk_size=chunk_size):
                    await f.write(chunk)
                    written += len(chunk)

        return written

    # ── Upload ────────────────────────────────────────────────────────────────

    async def complete_job(
        self,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
        srt_path: Path,
        json_path: Path,
        audio_duration_seconds: float,
        processing_time_seconds: float,
        segment_count: int,
        word_count: int,
    ) -> None:
        """
        Upload SRT and JSON outputs via multipart form.
        Coordinator validates ownership under FOR UPDATE — safe to retry.
        """
        metadata = json.dumps({
            "worker_id": str(worker_id),
            "audio_duration_seconds": round(audio_duration_seconds, 3),
            "processing_time_seconds": round(processing_time_seconds, 3),
            "rtf": round(processing_time_seconds / audio_duration_seconds, 4)
                   if audio_duration_seconds > 0 else 0,
            "segment_count": segment_count,
            "word_count": word_count,
        })

        with (
            open(srt_path, "rb") as srt_f,
            open(json_path, "rb") as json_f,
        ):
            resp = await self._http.post(
                f"/api/v1/worker/jobs/{job_id}/complete",
                data={"metadata": metadata},
                files={
                    "srt_file": (srt_path.name, srt_f, "application/x-subrip"),
                    "json_file": (json_path.name, json_f, "application/json"),
                },
                timeout=_UPLOAD_TIMEOUT,
            )
        resp.raise_for_status()

    async def fail_job(
        self,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
        error_message: str,
        error_category: str = "transient",
        retry: bool = True,
    ) -> None:
        resp = await self._http.post(
            f"/api/v1/worker/jobs/{job_id}/fail",
            json={
                "worker_id": str(worker_id),
                "error_message": error_message,
                "error_type": error_category.upper(),
                "error_category": error_category,
                "retry": retry,
            },
        )
        resp.raise_for_status()

    # ── Health check ──────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Returns True if coordinator is reachable."""
        try:
            resp = await self._http.get("/healthz", timeout=httpx.Timeout(5.0))
            return resp.status_code == 200
        except Exception:
            return False
