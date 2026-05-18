from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Any

from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..config import settings
from ..database import get_db_context
from ..models.input_directory import InputDirectory
from ..background import log_task_result
from ..websocket.manager import WebSocketManager

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 2.0  # Wait for file to stabilise before hashing
_QUEUE = "file_watcher"


class _MP3EventHandler(FileSystemEventHandler):
    """
    Synchronous watchdog handler. Enqueues events into a thread-safe asyncio Queue
    so the async loop can process them without blocking.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str]) -> None:
        self._loop = loop
        self._queue = queue

    def on_created(self, event: Any) -> None:
        if not event.is_directory and event.src_path.lower().endswith(".mp3"):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event.src_path)

    def on_moved(self, event: Any) -> None:
        if not event.is_directory and event.dest_path.lower().endswith(".mp3"):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event.dest_path)


class FileWatcherService:
    """
    Watches configured input directories for new .mp3 files and creates jobs.

    Design decisions:
    - watchdog Observer runs in a background thread (OS-native kqueue/FSEvents on macOS).
    - Events are handed off to the asyncio event loop via a queue.
    - Queue is created lazily in start() to avoid touching the event loop at __init__ time.
    - Debounce: file must have a stable size for DEBOUNCE_SECONDS before processing.
    - Duplicate detection: path-first (fast), then MD5 (thorough).
    - asyncio tasks created for file handling have an error callback attached so
      exceptions are not silently swallowed.
    """

    def __init__(self, ws_manager: WebSocketManager) -> None:
        self._ws = ws_manager
        self._observer = Observer()
        # Initialised in start() — must not create Queue before the event loop starts
        self._event_queue: asyncio.Queue[str] | None = None
        self._processor_task: asyncio.Task[None] | None = None
        self._running = False
        # Use the shared singleton — no mutable state, safe to share
        from ..queue.distributed_queue import distributed_queue as _job_svc
        self._job_service = _job_svc

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._event_queue = asyncio.Queue()
        handler = _MP3EventHandler(loop, self._event_queue)

        async with get_db_context() as db:
            directories = await self._load_active_directories(db)

        if not directories:
            logger.warning("No active input directories configured. File watcher idle.")
        else:
            watched = 0
            for d in directories:
                p = Path(d.path)
                if p.exists():
                    self._observer.schedule(handler, str(p), recursive=d.watch_recursively)
                    watched += 1
                else:
                    logger.warning("Input directory does not exist", extra={"path": str(p)})
            if watched:
                logger.info(
                    "File watcher watching %d input director%s",
                    watched,
                    "y" if watched == 1 else "ies",
                )

        self._running = True
        loop.run_in_executor(None, self._observer.start)
        self._processor_task = asyncio.create_task(
            self._process_queue(), name="file-watcher-processor"
        )
        self._processor_task.add_done_callback(
            lambda t: log_task_result(t, "file-watcher-processor")
        )
        logger.info("File watcher started")

    async def stop(self) -> None:
        self._running = False
        if self._processor_task and not self._processor_task.done():
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._observer.stop)
        await loop.run_in_executor(None, self._observer.join)
        logger.info("File watcher stopped")

    # ── Event processing ──────────────────────────────────────────────────────

    async def _process_queue(self) -> None:
        """Consume file events, debounce, and create jobs."""
        pending: dict[str, float] = {}
        assert self._event_queue is not None

        while self._running:
            try:
                while True:
                    try:
                        path = self._event_queue.get_nowait()
                        if path not in pending:
                            pending[path] = time.monotonic()
                    except asyncio.QueueEmpty:
                        break

                now = time.monotonic()
                ready = [p for p, t in pending.items() if (now - t) >= DEBOUNCE_SECONDS]

                for path_str in ready:
                    del pending[path_str]
                    task = asyncio.create_task(
                        self._handle_file(path_str),
                        name=f"handle-file-{Path(path_str).name}",
                    )
                    task.add_done_callback(
                        lambda t: log_task_result(t, "file-watcher-handle")
                    )

                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("File watcher queue processor error")
                await asyncio.sleep(1)

    async def _handle_file(self, path_str: str) -> None:
        """
        Validate, deduplicate, and create a job for a new MP3 file.
        Called as an asyncio task so errors don't crash the processor loop.
        """
        path = Path(path_str)

        if not path.exists() or not path.is_file():
            return

        # Confirm file size is stable (additional check beyond debounce)
        try:
            size1 = path.stat().st_size
            await asyncio.sleep(1.0)
            size2 = path.stat().st_size
            if size1 != size2:
                logger.debug("File still being written, skipping", extra={"path": path_str})
                return
        except OSError:
            return

        file_hash = await asyncio.get_running_loop().run_in_executor(
            None, self._compute_md5, path
        )
        file_size = path.stat().st_size

        # Resolve relative path from the matching input base directory
        input_path, relative_folder, priority = await self._resolve_path(path)
        if input_path is None:
            logger.warning("File not under any watched directory", extra={"path": path_str})
            return

        async with get_db_context() as db:
            job = await self._job_service.create_job(
                db=db,
                input_path=input_path,
                original_filename=path.name,
                relative_folder=relative_folder,
                file_size_bytes=file_size,
                file_hash=file_hash,
                priority=priority,
            )

        if job is not None:
            logger.info("New job created by file watcher", extra={
                "job_id": str(job.id), "path": input_path
            })
            await self._ws.emit_job_created(
                job_id=job.id,
                input_path=input_path,
            )
        else:
            logger.debug("File already queued, skipping", extra={"path": input_path})

    @staticmethod
    def _compute_md5(path: Path) -> str:
        md5 = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                md5.update(chunk)
        return md5.hexdigest()

    async def _resolve_path(
        self, path: Path
    ) -> tuple[str | None, str, int]:
        """
        Find which input directory this file belongs to.
        Returns (input_path, relative_folder, priority) or (None, "", 0).
        """
        async with get_db_context() as db:
            directories = await self._load_active_directories(db)

        for d in directories:
            base = Path(d.path)
            try:
                rel = path.relative_to(base)
                relative_folder = str(rel.parent) if rel.parent != Path(".") else ""
                return str(rel), relative_folder, d.default_priority
            except ValueError:
                continue

        return None, "", 0

    async def _load_active_directories(self, db: Any) -> list[InputDirectory]:
        from sqlalchemy import select

        result = await db.execute(
            select(InputDirectory).where(InputDirectory.is_active.is_(True))
        )
        return list(result.scalars().all())

    # ── Manual scan ───────────────────────────────────────────────────────────

    async def scan_directory(
        self,
        directory: InputDirectory,
        force_reprocess: bool = False,
    ) -> dict[str, int]:
        """Manually scan a directory and create jobs for all unprocessed MP3 files."""
        base = Path(directory.path)
        if not base.exists():
            raise FileNotFoundError(f"Directory not found: {base}")

        stats = {"created": 0, "skipped_duplicate": 0, "skipped_completed": 0, "total": 0}
        mp3_files = list(base.rglob("*.mp3")) + list(base.rglob("*.MP3"))
        stats["total"] = len(mp3_files)

        for path in mp3_files:
            try:
                rel = path.relative_to(base)
                input_path = str(rel)
                relative_folder = str(rel.parent) if rel.parent != Path(".") else ""

                file_hash = await asyncio.get_running_loop().run_in_executor(
                    None, self._compute_md5, path
                )

                async with get_db_context() as db:
                    job = await self._job_service.create_job(
                        db=db,
                        input_path=input_path,
                        original_filename=path.name,
                        relative_folder=relative_folder,
                        file_size_bytes=path.stat().st_size,
                        file_hash=file_hash,
                        priority=directory.default_priority,
                    )

                if job is not None:
                    stats["created"] += 1
                    await self._ws.emit_job_created(
                        job_id=job.id,
                        input_path=input_path,
                    )
                else:
                    stats["skipped_duplicate"] += 1

            except Exception:
                logger.exception("Error processing file during scan", extra={"path": str(path)})

        return stats
