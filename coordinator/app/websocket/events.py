from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from ..models.enums import JobStatus, WorkerStatus


# ── Dashboard outbound events ─────────────────────────────────────────────────


class ConnectedEvent(BaseModel):
    type: Literal["connected"] = "connected"
    coordinator_version: str


class HeartbeatEvent(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"
    timestamp: datetime


class JobCreatedEvent(BaseModel):
    type: Literal["job_created"] = "job_created"
    job_id: uuid.UUID
    input_path: str
    status: JobStatus
    created_at: datetime


class JobStatusChangedEvent(BaseModel):
    type: Literal["job_status_changed"] = "job_status_changed"
    job_id: uuid.UUID
    previous_status: JobStatus
    new_status: JobStatus
    worker_id: uuid.UUID | None
    worker_hostname: str | None
    timestamp: datetime


class JobProgressEvent(BaseModel):
    type: Literal["job_progress"] = "job_progress"
    job_id: uuid.UUID
    progress_percent: Decimal
    elapsed_seconds: float | None
    worker_id: uuid.UUID | None


class WorkerStatusChangedEvent(BaseModel):
    type: Literal["worker_status_changed"] = "worker_status_changed"
    worker_id: uuid.UUID
    hostname: str
    previous_status: WorkerStatus
    new_status: WorkerStatus
    timestamp: datetime


class WorkerMetricsEvent(BaseModel):
    type: Literal["worker_metrics"] = "worker_metrics"
    worker_id: uuid.UUID
    hostname: str
    cpu_percent: Decimal | None
    memory_percent: Decimal | None
    gpu_percent: Decimal | None
    current_job_progress: Decimal | None
    timestamp: datetime


class SystemAlertEvent(BaseModel):
    type: Literal["system_alert"] = "system_alert"
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    timestamp: datetime


# ── Worker inbound command types ──────────────────────────────────────────────


class WorkerConnectedEvent(BaseModel):
    type: Literal["connected"] = "connected"
    worker_id: uuid.UUID


class PauseJobCommand(BaseModel):
    type: Literal["PAUSE_JOB"] = "PAUSE_JOB"
    job_id: uuid.UUID


class ResumeJobCommand(BaseModel):
    type: Literal["RESUME_JOB"] = "RESUME_JOB"
    job_id: uuid.UUID


class CancelJobCommand(BaseModel):
    type: Literal["CANCEL_JOB"] = "CANCEL_JOB"
    job_id: uuid.UUID


class PingCommand(BaseModel):
    type: Literal["PING"] = "PING"


def build_event(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"type": event_type, "data": data}
