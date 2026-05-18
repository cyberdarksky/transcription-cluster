from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import ErrorCategory, JobStatus


# ── Read schemas (API responses) ──────────────────────────────────────────────


class JobEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: uuid.UUID
    worker_id: uuid.UUID | None
    event_type: str
    details: dict[str, Any] | None
    created_at: datetime


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    input_path: str
    original_filename: str
    relative_folder: str
    status: JobStatus
    priority: int
    retry_count: int
    max_retries: int
    progress_percent: Decimal | None
    file_size_bytes: int | None
    file_hash: str | None
    error_category: ErrorCategory | None
    last_error: str | None
    worker_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    assigned_at: datetime | None
    started_at: datetime | None
    paused_at: datetime | None
    completed_at: datetime | None
    audio_duration_seconds: Decimal | None
    processing_time_seconds: Decimal | None
    rtf: Decimal | None
    output_srt_path: str | None
    output_json_path: str | None
    output_srt_hash: str | None
    output_json_hash: str | None
    worker_hostname: str | None = None


class JobReadDetail(JobRead):
    """Extended job schema including event history (returned by GET /jobs/{id})."""

    events: list[JobEventRead] = Field(default_factory=list)


# ── Write schemas (request bodies) ───────────────────────────────────────────


class JobPauseResponse(BaseModel):
    id: uuid.UUID
    status: JobStatus
    command_delivered: bool
    message: str


class JobRetryResponse(BaseModel):
    id: uuid.UUID
    status: JobStatus
    retry_count: int


# ── Worker internal schemas ───────────────────────────────────────────────────


class WhisperSettings(BaseModel):
    model: str
    language: str
    word_timestamps: bool


class JobAssignment(BaseModel):
    """Returned to worker on successful job claim."""

    job_id: uuid.UUID
    input_path: str
    original_filename: str
    relative_folder: str
    file_size_bytes: int | None
    download_url: str
    whisper_settings: WhisperSettings
    max_job_duration_seconds: int | None


class JobStartRequest(BaseModel):
    worker_id: uuid.UUID


class JobProgressRequest(BaseModel):
    worker_id: uuid.UUID
    percent: Decimal = Field(ge=0, le=100)
    elapsed_seconds: float | None = None


class JobProgressResponse(BaseModel):
    received: bool
    command: str | None = None  # "PAUSE" | "CANCEL" | None


class CompletionMetadata(BaseModel):
    worker_id: uuid.UUID
    audio_duration_seconds: Decimal
    processing_time_seconds: Decimal
    rtf: Decimal
    segment_count: int
    word_count: int


class JobCompleteResponse(BaseModel):
    status: JobStatus
    output_srt_path: str
    output_json_path: str


class JobFailRequest(BaseModel):
    worker_id: uuid.UUID
    error_message: str
    error_type: str
    error_category: ErrorCategory = ErrorCategory.TRANSIENT
    retry: bool = True


class JobFailResponse(BaseModel):
    status: JobStatus
    retry_count: int
    will_retry: bool
    retry_after: datetime | None
