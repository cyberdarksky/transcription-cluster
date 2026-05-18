from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AnyUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://localhost/transcription_cluster",
        description="PostgreSQL connection URL (asyncpg driver)",
    )
    db_echo: bool = Field(default=False, description="SQLAlchemy query logging")
    db_pool_size: int = Field(default=20)
    db_max_overflow: int = Field(default=10)
    db_pool_timeout: int = Field(default=30)

    # ── Server ────────────────────────────────────────────────────────────────
    coordinator_host: str = Field(default="0.0.0.0")
    coordinator_port: int = Field(default=8080)
    reload: bool = Field(default=False, description="Uvicorn hot reload (dev only)")

    # ── File storage (absolute paths) ─────────────────────────────────────────
    input_base_dir: Path = Field(default=Path("/opt/transcription-data/input"))
    output_base_dir: Path = Field(default=Path("/opt/transcription-data/output"))
    static_dir: Path = Field(
        default=Path(__file__).parent.parent / "static",
        description="Built React app directory",
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )
    json_logs: bool = Field(default=True, description="Emit JSON-formatted log lines")

    # ── Worker monitoring ─────────────────────────────────────────────────────
    worker_heartbeat_timeout_seconds: int = Field(
        default=90,
        description="Worker marked offline if no heartbeat within this window",
    )
    recovery_grace_seconds: int = Field(
        default=30,
        description=(
            "Grace period after coordinator restart before recover_stale_jobs() runs. "
            "Allows reconnecting workers to report current_job_id."
        ),
    )

    # ── Retry defaults ────────────────────────────────────────────────────────
    max_retries_default: int = Field(default=3)
    retry_delays_seconds: list[int] = Field(default=[0, 60, 300])
    job_timeout_multiplier: int = Field(
        default=5,
        description="max_job_duration = audio_duration * this factor",
    )

    # ── Lease system ──────────────────────────────────────────────────────────
    # Each lease grant lasts this long. Workers must renew (via heartbeat) before expiry.
    job_lease_duration_seconds: int = Field(
        default=300,
        description="Seconds before a worker lease expires if not renewed (default 5 min)",
    )
    # Recovery service checks for expired leases this often.
    lease_recovery_interval_seconds: int = Field(default=30)
    # Retry scheduler checks for ready retry_wait jobs this often.
    retry_scheduler_interval_seconds: int = Field(default=30)

    # ── Maintenance ───────────────────────────────────────────────────────────
    worker_metrics_retention_days: int = Field(default=7)
    job_events_retention_days: int = Field(default=90)

    # ── Whisper model (local path set by worker; coordinator stores for reference) ──
    # Workers read this from their own config.env, not from the coordinator.
    # Used only when coordinator generates WhisperSettings for job assignments.
    whisper_model_path: str = Field(
        default="/opt/transcription-models/current",
        description="Absolute local path to the mlx-whisper model bundle on worker machines",
    )

    # ── Service metadata ──────────────────────────────────────────────────────
    coordinator_version: str = Field(default="1.0.0")
    service_name: str = Field(default="TranscriptionCluster")

    @field_validator("input_base_dir", "output_base_dir", mode="before")
    @classmethod
    def coerce_path(cls, v: object) -> Path:
        return Path(str(v))

    @property
    def sync_database_url(self) -> str:
        """Synchronous URL for Alembic migrations."""
        return self.database_url.replace("+asyncpg", "").replace(
            "postgresql+asyncpg", "postgresql"
        )


settings = Settings()
