from fastapi import APIRouter

from .files import router as files_router
from .jobs import router as jobs_router
from .system import router as system_router
from .worker_internal import router as worker_internal_router
from .workers import router as workers_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(jobs_router)
api_router.include_router(workers_router)
api_router.include_router(worker_internal_router)
api_router.include_router(files_router)
api_router.include_router(system_router)
