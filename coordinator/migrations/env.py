from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Make 'app' importable from migrations/ ───────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.models.base import Base

# Import all models so their tables are registered in Base.metadata
from app.models.input_directory import InputDirectory  # noqa: F401
from app.models.job import Job  # noqa: F401
from app.models.job_event import JobEvent  # noqa: F401
from app.models.system_setting import SystemSetting  # noqa: F401
from app.models.worker import Worker  # noqa: F401
from app.models.worker_metric import WorkerMetric  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from our settings (supports DATABASE_URL env var)
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
