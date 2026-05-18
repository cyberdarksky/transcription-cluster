"""
Test infrastructure for the distributed queue system.

Database strategy:
    - Tests use a dedicated PostgreSQL database (TEST_DATABASE_URL env var).
    - Tables are created once per test session from the SQLAlchemy metadata.
    - Each test runs inside a SAVEPOINT that is rolled back on teardown,
      giving transactional isolation without recreating tables.
    - Race-condition tests that require multiple DB connections cannot use
      SAVEPOINTs — they use the `clean_db` fixture which truncates tables instead.

Running:
    TEST_DATABASE_URL=postgresql+asyncpg://localhost/transcription_test pytest tests/ -v

Skip if no PostgreSQL:
    If TEST_DATABASE_URL is unset and localhost PostgreSQL is unavailable, all
    tests are automatically skipped.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Import all models so Base.metadata is fully populated
from app.models.base import Base
from app.models.enums import JobStatus, WorkerStatus
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.worker import Worker
from app.models.worker_metric import WorkerMetric
from app.models.input_directory import InputDirectory
from app.models.system_setting import SystemSetting

UTC = timezone.utc

_DEFAULT_URL = "postgresql+asyncpg://localhost/transcription_test"
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", _DEFAULT_URL)


# ── Event loop ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Single event loop for the entire test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


# ── Engine — created once per session ────────────────────────────────────────

@pytest_asyncio.fixture(scope="session")
async def async_engine() -> AsyncGenerator[AsyncEngine, None]:
    """
    Create the test engine and schema once.
    If the database is unreachable, all tests are skipped.
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, pool_size=5)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


# ── Per-test transactional isolation (most tests) ────────────────────────────

@pytest_asyncio.fixture
async def db_conn(async_engine: AsyncEngine) -> AsyncGenerator[AsyncConnection, None]:
    """
    Wraps each test in a connection-level transaction that is rolled back
    after the test, giving clean isolation without table recreation overhead.
    """
    async with async_engine.connect() as conn:
        await conn.begin()
        yield conn
        await conn.rollback()


@pytest_asyncio.fixture
async def db_session(db_conn: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    """
    Async session bound to the test connection (shares the rolled-back tx).
    Use this for all single-session tests.
    """
    session_factory = async_sessionmaker(
        bind=db_conn,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session


# ── Multi-connection fixture (race condition tests) ───────────────────────────

@pytest_asyncio.fixture
async def clean_db(async_engine: AsyncEngine) -> AsyncGenerator[AsyncEngine, None]:
    """
    Truncates all tables before the test. Used for tests that require multiple
    independent DB connections (e.g., concurrent claim tests with FOR UPDATE
    SKIP LOCKED), where the shared-transaction approach cannot be used.
    """
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE workers, jobs, job_events, worker_metrics, "
                "input_directories, system_settings RESTART IDENTITY CASCADE"
            )
        )
    yield async_engine
    # No teardown: next test using clean_db will truncate again.


# ── Factory helpers ───────────────────────────────────────────────────────────

async def make_worker(
    session: AsyncSession,
    hostname: str = "test-worker",
    **kwargs: Any,
) -> Worker:
    worker = Worker(
        stable_worker_id=uuid.uuid4(),
        hostname=hostname,
        mac_address=f"AA:BB:{uuid.uuid4().hex[:10].upper()}",
        ip_address="192.168.1.100",
        status=WorkerStatus.IDLE,
        **kwargs,
    )
    session.add(worker)
    await session.flush()
    return worker


async def make_job(
    session: AsyncSession,
    input_path: str | None = None,
    status: JobStatus = JobStatus.QUEUED,
    priority: int = 0,
    **kwargs: Any,
) -> Job:
    job = Job(
        input_path=input_path or f"test/{uuid.uuid4().hex}.mp3",
        original_filename="test.mp3",
        relative_folder="test",
        status=status,
        priority=priority,
        **kwargs,
    )
    session.add(job)
    await session.flush()
    return job
