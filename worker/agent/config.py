from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_STABLE_ID_FILE = Path.home() / ".transcription-worker" / "worker-id"
_DEFAULT_MODEL_PATH = Path("/opt/transcription-models/current")
_DEFAULT_TEMP_DIR = Path("/tmp/transcription-jobs")


class WorkerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Coordinator connection ────────────────────────────────────────────────
    # If unset, mDNS auto-discovery is used.
    coordinator_host: str | None = Field(default=None)
    coordinator_port: int = Field(default=8080)
    # mDNS service type to browse for
    mdns_service_type: str = Field(default="_transcription._tcp.local.")
    # How long to wait for mDNS before falling back to cached URL (seconds)
    mdns_discovery_timeout_seconds: int = Field(default=60)

    # ── Worker identity ───────────────────────────────────────────────────────
    # Stable UUID stored on disk — survives MAC address changes, VPN, Docker.
    stable_worker_id_file: Path = Field(default=_DEFAULT_STABLE_ID_FILE)

    # ── Whisper model ─────────────────────────────────────────────────────────
    # Absolute path to a local model bundle (see model_store.py).
    # Default 'current' symlink is updated atomically on model upgrades.
    # HuggingFace repo IDs are rejected at startup (offline-only).
    model_path: Path = Field(default=_DEFAULT_MODEL_PATH)
    whisper_language: str = Field(default="tr")
    whisper_word_timestamps: bool = Field(default=True)

    # ── Storage ───────────────────────────────────────────────────────────────
    temp_dir: Path = Field(default=_DEFAULT_TEMP_DIR)

    # ── Timing ───────────────────────────────────────────────────────────────
    heartbeat_interval_seconds: int = Field(default=30)
    job_poll_interval_seconds: int = Field(default=5)
    reconnect_base_delay_seconds: float = Field(default=5.0)
    reconnect_max_delay_seconds: float = Field(default=120.0)
    reconnect_jitter_factor: float = Field(default=0.2)
    download_chunk_size_bytes: int = Field(default=1_048_576)  # 1 MB
    upload_timeout_seconds: int = Field(default=300)
    # Subprocess kill patience before SIGKILL (seconds)
    subprocess_term_timeout_seconds: float = Field(default=5.0)
    # max_job_duration = audio_duration * this multiplier (used when coordinator
    # doesn't supply max_job_duration_seconds in the job assignment).
    job_timeout_multiplier: int = Field(
        default=5,
        description="max_job_duration = audio_duration * this factor (safety timeout)",
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )
    json_logs: bool = Field(default=True)

    @field_validator("model_path", "temp_dir", "stable_worker_id_file", mode="before")
    @classmethod
    def coerce_path(cls, v: object) -> Path:
        return Path(str(v))

    @property
    def coordinator_base_url(self) -> str | None:
        if self.coordinator_host:
            return f"http://{self.coordinator_host}:{self.coordinator_port}"
        return None

    @property
    def coordinator_ws_url(self) -> str | None:
        if self.coordinator_host:
            return f"ws://{self.coordinator_host}:{self.coordinator_port}"
        return None


def get_or_create_stable_worker_id(config: WorkerConfig) -> uuid.UUID:
    """
    Return the stable worker UUID, creating it on first run.
    Stored at stable_worker_id_file (default: ~/.transcription-worker/worker-id).
    Survives MAC address changes, VPN interfaces, hardware swap.
    """
    id_file = config.stable_worker_id_file
    id_file.parent.mkdir(parents=True, exist_ok=True)

    if id_file.exists():
        raw = id_file.read_text().strip()
        if raw:
            try:
                return uuid.UUID(raw)
            except ValueError:
                pass  # Corrupted — regenerate

    new_id = uuid.uuid4()
    id_file.write_text(str(new_id))
    return new_id
