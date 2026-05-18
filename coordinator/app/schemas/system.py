from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobStats(BaseModel):
    total: int
    pending: int
    assigned: int
    processing: int
    paused: int
    completed: int
    failed: int
    cancelled: int


class WorkerStats(BaseModel):
    total: int
    online: int
    offline: int
    busy: int
    idle: int


class ThroughputStats(BaseModel):
    jobs_completed_last_1h: int
    jobs_completed_last_24h: int
    audio_hours_last_24h: float
    avg_rtf_last_24h: float | None


class CoordinatorInfo(BaseModel):
    version: str
    uptime_seconds: float
    db_connected: bool
    input_dirs_active: int
    storage_used_gb: float | None
    storage_available_gb: float | None


class SystemStatsResponse(BaseModel):
    jobs: JobStats
    workers: WorkerStats
    throughput: ThroughputStats
    coordinator: CoordinatorInfo


class SystemSettingsResponse(BaseModel):
    worker_heartbeat_timeout_seconds: int
    max_retries_default: int
    retry_delay_seconds: list[int]
    worker_metrics_retention_days: int
    job_events_retention_days: int
    dashboard_refresh_interval_ms: int
    file_watcher_debounce_seconds: int
    whisper_model: str
    whisper_language: str
    whisper_word_timestamps: bool
    job_timeout_multiplier: int
    coordinator_recovery_grace_seconds: int


class SystemSettingsUpdate(BaseModel):
    """Partial update — only provided keys are changed."""

    worker_heartbeat_timeout_seconds: int | None = None
    max_retries_default: int | None = None
    retry_delay_seconds: list[int] | None = None
    worker_metrics_retention_days: int | None = None
    job_events_retention_days: int | None = None
    job_timeout_multiplier: int | None = None


class InputDirectoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    path: str
    output_path: str
    is_active: bool
    watch_recursively: bool
    default_priority: int
    label: str | None
    created_at: datetime
    updated_at: datetime


class InputDirectoryCreate(BaseModel):
    path: str
    output_path: str
    label: str | None = None
    watch_recursively: bool = True
    default_priority: int = Field(default=0, ge=-100, le=100)


class ScanRequest(BaseModel):
    input_directory_id: uuid.UUID | None = None
    force_reprocess: bool = False


class ScanResponse(BaseModel):
    scan_id: str
    status: str
    message: str


class BulkJobCreateRequest(BaseModel):
    input_directory_id: uuid.UUID
    priority: int = Field(default=0, ge=-100, le=100)
    force_reprocess: bool = False


class BulkJobCreateResponse(BaseModel):
    created: int
    skipped_duplicate: int
    skipped_completed: int
    total_scanned: int
