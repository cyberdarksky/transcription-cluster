from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.v1.router import api_router
from .config import settings
from .database import check_db_connection, engine
from .logging_config import setup_logging
from .services.file_watcher import FileWatcherService
from .services.maintenance import MaintenanceService
from .services.mdns_announcer import MDNSAnnouncer
from .services.worker_monitor import WorkerMonitor
from .websocket.manager import WebSocketManager

# ── Logging must be configured before any other imports log ──────────────────
setup_logging(log_level=settings.log_level, json_logs=settings.json_logs)
logger = logging.getLogger(__name__)


# ── Application lifespan ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / shutdown lifecycle manager.

    Startup order matters:
    1. Verify DB is reachable.
    2. Run coordinator restart recovery (after grace period).
    3. Start background services.
    4. Start mDNS — workers can now discover and connect.

    Shutdown order is reverse: stop accepting new connections, drain, clean up.
    """
    ws_manager = WebSocketManager()
    app.state.ws_manager = ws_manager
    app.state.recovery_grace_active = True

    # ── Verify database connectivity ──────────────────────────────────────────
    logger.info("Verifying database connection...")
    if not await check_db_connection():
        logger.error("Cannot reach PostgreSQL! Check DATABASE_URL and that PostgreSQL is running.")
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

    app.state.worker_monitor = worker_monitor
    app.state.file_watcher = file_watcher
    app.state.maintenance = maintenance
    app.state.mdns = mdns

    await worker_monitor.start()
    await file_watcher.start()
    await maintenance.start()

    # ── Grace period: give reconnecting workers time to report current_job_id ─
    logger.info(
        "Coordinator restart grace period started (%ds)", settings.recovery_grace_seconds
    )
    asyncio.create_task(_end_grace_period(app, ws_manager), name="grace-period")

    # ── mDNS announcement: workers can now discover the coordinator ───────────
    await mdns.start()

    logger.info(
        "Coordinator started",
        extra={
            "version": settings.coordinator_version,
            "port": settings.coordinator_port,
            "input_dir": str(settings.input_base_dir),
        },
    )

    yield  # ── Application running ────────────────────────────────────────────

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Coordinator shutting down...")
    await mdns.stop()
    await worker_monitor.stop()
    await file_watcher.stop()
    await maintenance.stop()
    await engine.dispose()
    logger.info("Coordinator shutdown complete")


async def _end_grace_period(app: FastAPI, ws_manager: WebSocketManager) -> None:
    """
    After recovery_grace_seconds, mark grace period as over and run
    recover_stale_jobs() for workers that did NOT reconnect.
    """
    await asyncio.sleep(settings.recovery_grace_seconds)
    app.state.recovery_grace_active = False

    from .database import get_db_context
    from sqlalchemy import text

    async with get_db_context() as db:
        result = await db.execute(text("SELECT recover_stale_jobs()"))
        recovered = result.scalar()

    logger.info("Grace period ended; stale jobs recovered: %d", recovered or 0)
    if recovered:
        await ws_manager.emit_system_alert(
            severity="info",
            code="STALE_JOBS_RECOVERED",
            message=f"Koordinatör yeniden başlatması: {recovered} iş yeniden kuyruğa alındı",
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
        db_ok = await check_db_connection()
        ready = db_ok and not grace_active
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"ready": ready, "grace_period": grace_active, "db": db_ok},
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
                    # Send keepalive heartbeat
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
                        logger.debug("Worker ACK received", extra={
                            "worker_id": worker_id, "type": msg_type
                        })
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

    # ── Static files (React dashboard — must be last) ─────────────────────────
    if settings.static_dir.exists() and any(settings.static_dir.iterdir()):
        app.mount("/", StaticFiles(directory=str(settings.static_dir), html=True), name="static")
        logger.info("Dashboard static files mounted from %s", settings.static_dir)
    else:
        logger.info("No static files found at %s — dashboard not served", settings.static_dir)

    return app


app = create_app()
