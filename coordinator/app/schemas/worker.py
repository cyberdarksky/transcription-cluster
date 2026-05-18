from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import WorkerStatus


# ── Read schemas ──────────────────────────────────────────────────────────────


class WorkerMetricSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recorded_at: datetime
    cpu_percent: Decimal | None
    memory_percent: Decimal | None
    memory_used_gb: Decimal | None
    gpu_percent: Decimal | None


class WorkerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stable_worker_id: uuid.UUID | None
    hostname: str
    ip_address: str
    status: WorkerStatus
    cpu_model: str | None
    cpu_cores: int | None
    memory_total_gb: Decimal | None
    gpu_model: str | None
    whisper_backend: str
    worker_version: str | None
    last_heartbeat: datetime | None
    current_job_id: uuid.UUID | None
    jobs_completed: int
    jobs_failed: int
    total_audio_seconds: Decimal
    average_rtf: Decimal | None
    registered_at: datetime
    updated_at: datetime


class WorkerReadDetail(WorkerRead):
    metrics_history: list[WorkerMetricSnapshot] = Field(default_factory=list)


# ── Registration ──────────────────────────────────────────────────────────────


class WorkerRegisterRequest(BaseModel):
    stable_worker_id: uuid.UUID | None = None
    hostname: str
    mac_address: str
    ip_address: str
    api_port: int = 8081
    cpu_model: str | None = None
    cpu_cores: int | None = None
    memory_total_gb: Decimal | None = None
    gpu_model: str | None = None
    whisper_backend: str = "mlx-whisper"
    worker_version: str | None = None
    # Reconnect context: worker reports its current job so coordinator
    # does not reassign it during the grace period.
    current_job_id: uuid.UUID | None = None
    current_job_status: str | None = None


class WorkerRegisterResponse(BaseModel):
    worker_id: uuid.UUID
    heartbeat_interval_seconds: int
    coordinator_version: str
    websocket_url: str
    # True if coordinator just restarted and is in grace period.
    recovery_grace_active: bool
    # True if the worker should kill its current job (it was reassigned).
    cancel_current_job: bool
    settings: dict[str, Any]


# ── Heartbeat ─────────────────────────────────────────────────────────────────


class WorkerMetricsPayload(BaseModel):
    cpu_percent: Decimal | None = None
    memory_used_gb: Decimal | None = None
    memory_total_gb: Decimal | None = None
    memory_percent: Decimal | None = None
    gpu_percent: Decimal | None = None
    gpu_memory_used_gb: Decimal | None = None


class WorkerHeartbeatRequest(BaseModel):
    worker_id: uuid.UUID
    status: WorkerStatus
    current_job_id: uuid.UUID | None = None
    job_progress_percent: Decimal | None = None
    metrics: WorkerMetricsPayload = Field(default_factory=WorkerMetricsPayload)


class PendingCommand(BaseModel):
    command: str
    job_id: uuid.UUID | None = None


class WorkerHeartbeatResponse(BaseModel):
    received_at: datetime
    pending_commands: list[PendingCommand] = Field(default_factory=list)
