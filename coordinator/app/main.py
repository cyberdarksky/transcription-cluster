from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.v1.router import api_router
from .background import stop_with_timeout
from .config import settings
from .database import check_db_connection, engine
from .logging_config import setup_logging
from .queue.recovery_service import LeaseRecoveryService
from .queue.retry_scheduler import RetryScheduler
from .services.file_watcher import FileWatcherService
from .services.maintenance import MaintenanceService
from .services.mdns_announcer import MDNSAnnouncer
from .services.worker_monitor import WorkerMonitor
from .websocket.manager import WebSocketManager

# ── Logging must be configured before any other imports log ──────────────────
setup_logging(log_level=settings.log_level, json_logs=settings.json_logs)
logger = logging.getLogger(__name__)

_SHUTDOWN_SERVICE_TIMEOUT = 10.0


# ── Application lifespan ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / shutdown lifecycle manager.

    Startup order matters:
    1. Verify DB is reachable.
    2. Start background services.
    3. Start mDNS — workers can now discover and connect.
    4. After grace period, recover workers that did not check in.

    Shutdown order is reverse: cancel grace, stop accepting work, drain, clean up.
    """
    ws_manager = WebSocketManager()
    app.state.ws_manager = ws_manager
    app.state.recovery_grace_active = True
    app.state.shutting_down = False
    app.state.coordinator_started_at = datetime.now(timezone.utc)
    app.state.grace_period_task = None

    # ── Verify database connectivity ──────────────────────────────────────────
    logger.info("Verifying database connection...")
    if not await check_db_connection():
        logger.error(
            "Cannot reach PostgreSQL. Check DATABASE_URL and that PostgreSQL is running."
        )
        raise RuntimeError("Database connection failed at startup")
    logger.info("Database connection OK")

    # ── Create required directories ───────────────────────────────────────────
    settings.input_base_dir.mkdir(parents=True, exist_ok=True)
    settings.output_base_dir.mkdir(parents=True, exist_ok=True)

    # ── Background services ───────────────────────────────────────────────────
    worker_monitor = WorkerMonitor(ws_manager)
    file_watcher = FileWatcherService(ws_manager)
    maintenance = MaintenanceService()
    mdns = MDNSAnnouncer()
    lease_recovery = LeaseRecoveryService(ws_manager)
    retry_scheduler = RetryScheduler(ws_manager)

    app.state.worker_monitor = worker_monitor
    app.state.file_watcher = file_watcher
    app.state.maintenance = maintenance
    app.state.mdns = mdns
    app.state.lease_recovery = lease_recovery
    app.state.retry_scheduler = retry_scheduler

    await worker_monitor.start()
    await file_watcher.start()
    await maintenance.start()
    await lease_recovery.start()
    await retry_scheduler.start()

    # ── Grace period: reconnecting workers report current_job_id ─────────────
    logger.info(
        "Coordinator restart grace period started (%ds)",
        settings.recovery_grace_seconds,
    )
    app.state.grace_period_task = asyncio.create_task(
        _end_grace_period(app, ws_manager, worker_monitor),
        name="grace-period",
    )

    # ── mDNS announcement: workers can now discover the coordinator ─────────
    await mdns.start()

    logger.info(
        "Coordinator started",
        extra={
            "version": settings.coordinator_version,
            "port": settings.coordinator_port,
            "input_dir": str(settings.input_base_dir),
            "grace_seconds": settings.recovery_grace_seconds,
        },
    )

    yield  # ── Application running ────────────────────────────────────────────

    # ── Shutdown ──────────────────────────────────────────────────────────────
    app.state.shutting_down = True
    app.state.recovery_grace_active = False
    logger.info("Coordinator shutting down...")

    grace_task = app.state.grace_period_task
    if grace_task is not None and not grace_task.done():
        grace_task.cancel()
        try:
            await grace_task
        except asyncio.CancelledError:
            pass

    await stop_with_timeout(mdns.stop(), "mDNS", timeout=_SHUTDOWN_SERVICE_TIMEOUT)
    await stop_with_timeout(
        retry_scheduler.stop(), "retry-scheduler", timeout=_SHUTDOWN_SERVICE_TIMEOUT
    )
    await stop_with_timeout(
        lease_recovery.stop(), "lease-recovery", timeout=_SHUTDOWN_SERVICE_TIMEOUT
    )
    await stop_with_timeout(
        worker_monitor.stop(), "worker-monitor", timeout=_SHUTDOWN_SERVICE_TIMEOUT
    )
    await stop_with_timeout(
        file_watcher.stop(), "file-watcher", timeout=_SHUTDOWN_SERVICE_TIMEOUT
    )
    await stop_with_timeout(
        maintenance.stop(), "maintenance", timeout=_SHUTDOWN_SERVICE_TIMEOUT
    )

    await engine.dispose()
    logger.info("Coordinator shutdown complete")


async def _end_grace_period(
    app: FastAPI,
    ws_manager: WebSocketManager,
    worker_monitor: WorkerMonitor,
) -> None:
    """
    After recovery_grace_seconds, mark grace period over and recover workers
    that did not check in since this coordinator process started.
    """
    try:
        await asyncio.sleep(settings.recovery_grace_seconds)
    except asyncio.CancelledError:
        logger.debug("Grace period cancelled (coordinator shutting down)")
        return

    if getattr(app.state, "shutting_down", False):
        return

    app.state.recovery_grace_active = False

    try:
        recovered = await worker_monitor.run_post_grace_recovery(
            app.state.coordinator_started_at
        )
    except Exception:
        logger.exception("Post-grace recovery failed")
        return

    logger.info("Grace period ended; stale jobs recovered: %d", recovered)
    if recovered:
        await ws_manager.emit_system_alert(
            severity="info",
            code="STALE_JOBS_RECOVERED",
            message=(
                f"Koordinatör yeniden başlatması: {recovered} iş yeniden kuyruğa alındı"
            ),
        )


# ── FastAPI application ───────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Transkripsiyon Kümesi Koordinatörü",
        version=settings.coordinator_version,
        description=(
            "Dağıtık Apple Silicon transkripsiyon kümesi için merkezi koordinatör. "
            "İş kuyruğu, işçi yönetimi ve gerçek zamanlı izleme."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS (for dashboard on same origin this is a no-op in prod) ──────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API routes ────────────────────────────────────────────────────────────
    app.include_router(api_router)

    # ── Health endpoints ──────────────────────────────────────────────────────
    @app.get("/healthz", tags=["health"], include_in_schema=False)
    async def health() -> dict:
        db_ok = await check_db_connection()
        return {
            "status": "ok" if db_ok else "degraded",
            "db": "ok" if db_ok else "unreachable",
            "version": settings.coordinator_version,
        }

    @app.get("/readyz", tags=["health"], include_in_schema=False)
    async def ready(request: Request) -> JSONResponse:
        grace_active = getattr(request.app.state, "recovery_grace_active", True)
        shutting_down = getattr(request.app.state, "shutting_down", False)
        db_ok = await check_db_connection()
        ready = db_ok and not grace_active and not shutting_down
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "ready": ready,
                "grace_period": grace_active,
                "shutting_down": shutting_down,
                "db": db_ok,
            },
        )

    # ── WebSocket: Dashboard ──────────────────────────────────────────────────
    @app.websocket("/ws/dashboard")
    async def dashboard_ws(websocket: WebSocket) -> None:
        ws_manager: WebSocketManager = websocket.app.state.ws_manager
        await ws_manager.connect_dashboard(websocket)
        try:
            await websocket.send_json({
                "type": "connected",
                "coordinator_version": settings.coordinator_version,
            })
            while True:
                try:
                    data = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
                    if data.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                except asyncio.TimeoutError:
                    from datetime import datetime, timezone
                    await websocket.send_json({
                        "type": "heartbeat",
                        "data": {"timestamp": datetime.now(timezone.utc).isoformat()},
                    })
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Dashboard WebSocket error")
        finally:
            ws_manager.disconnect_dashboard(websocket)

    # ── WebSocket: Worker ─────────────────────────────────────────────────────
    @app.websocket("/ws/worker")
    async def worker_ws(websocket: WebSocket, worker_id: str) -> None:
        ws_manager: WebSocketManager = websocket.app.state.ws_manager
        await ws_manager.connect_worker(websocket, worker_id)
        try:
            await websocket.send_json({"type": "connected", "worker_id": worker_id})
            while True:
                try:
                    data = await asyncio.wait_for(websocket.receive_json(), timeout=60.0)
                    msg_type = data.get("type", "")
                    if msg_type in ("PAUSE_ACK", "RESUME_ACK", "CANCEL_ACK", "PONG"):
                        logger.debug(
                            "Worker ACK received",
                            extra={"worker_id": worker_id, "type": msg_type},
                        )
                    elif msg_type == "PING":
                        await websocket.send_json({"type": "PONG"})
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "PING"})
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Worker WebSocket error", extra={"worker_id": worker_id})
        finally:
            ws_manager.disconnect_worker(worker_id)

    # ── Dashboard (React SPA — must be last) ─────────────────────────────────
    _register_dashboard_static(app)

    return app


def _register_dashboard_static(app: FastAPI) -> None:
    """
    Serve the built React app with SPA fallback.

    StaticFiles(html=True) only serves index.html for directory URLs; refreshing
    /queue or /workers would 404 without an explicit fallback to index.html.
    """
    static_dir = settings.static_dir
    index_html = static_dir / "index.html"
    assets_dir = static_dir / "assets"

    if not index_html.exists():
        logger.info("No static files found at %s — dashboard not served", static_dir)
        return

    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="dashboard-assets",
        )

    _spa_skip_prefixes = ("api/", "ws/", "assets/", "docs", "redoc", "openapi.json")

    @app.get("/", include_in_schema=False)
    async def dashboard_root() -> FileResponse:
        return FileResponse(index_html)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def dashboard_spa(full_path: str) -> FileResponse:
        if full_path.startswith(_spa_skip_prefixes):
            raise HTTPException(status_code=404, detail="Not Found")
        file_path = static_dir / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(index_html)

    logger.info("Dashboard SPA served from %s", static_dir)


app = create_app()
