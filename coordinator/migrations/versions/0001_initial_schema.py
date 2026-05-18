"""Initial schema — all tables

Revision ID: 0001
Revises:
Create Date: 2026-05-18

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extensions ────────────────────────────────────────────────────────────
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')

    # ── workers ───────────────────────────────────────────────────────────────
    # NOTE: current_job_id FK is added in a later migration after jobs exists.
    op.create_table(
        "workers",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("stable_worker_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("hostname", sa.Text(), nullable=False),
        sa.Column("mac_address", sa.String(17), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column("api_port", sa.Integer(), nullable=False, server_default="8081"),
        sa.Column("status", sa.String(20), nullable=False, server_default="offline"),
        sa.Column("cpu_model", sa.Text(), nullable=True),
        sa.Column("cpu_cores", sa.Integer(), nullable=True),
        sa.Column("memory_total_gb", sa.Numeric(6, 2), nullable=True),
        sa.Column("gpu_model", sa.Text(), nullable=True),
        sa.Column("whisper_backend", sa.String(50), nullable=False, server_default="mlx-whisper"),
        sa.Column("worker_version", sa.String(20), nullable=True),
        sa.Column("last_heartbeat", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("heartbeat_interval_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("current_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("jobs_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_audio_seconds", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("total_processing_seconds", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("average_rtf", sa.Numeric(6, 4), nullable=True),
        sa.Column("registered_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mac_address"),
        sa.UniqueConstraint("stable_worker_id"),
    )
    op.create_index("idx_workers_status", "workers", ["status"])
    op.create_index("idx_workers_mac_address", "workers", ["mac_address"])
    op.create_index(
        "idx_workers_last_heartbeat",
        "workers",
        ["last_heartbeat"],
        postgresql_where=sa.text("status != 'offline'"),
    )
    op.create_index("idx_workers_stable_id", "workers", ["stable_worker_id"])

    # ── jobs ──────────────────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("input_path", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("relative_folder", sa.Text(), nullable=False, server_default=""),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("file_hash", sa.String(32), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("error_category", sa.String(15), nullable=True),
        sa.Column("next_retry_after", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("max_job_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("progress_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("assigned_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("paused_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("output_srt_path", sa.Text(), nullable=True),
        sa.Column("output_json_path", sa.Text(), nullable=True),
        sa.Column("output_srt_hash", sa.String(32), nullable=True),
        sa.Column("output_json_hash", sa.String(32), nullable=True),
        sa.Column("audio_duration_seconds", sa.Numeric(10, 2), nullable=True),
        sa.Column("processing_time_seconds", sa.Numeric(10, 2), nullable=True),
        sa.Column("rtf", sa.Numeric(6, 4), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('pending','assigned','processing','paused','completed','failed','cancelled')",
            name="jobs_status_valid",
        ),
        sa.CheckConstraint("retry_count >= 0", name="jobs_retry_count_valid"),
        sa.CheckConstraint("priority BETWEEN -100 AND 100", name="jobs_priority_range"),
        sa.CheckConstraint(
            "progress_percent IS NULL OR progress_percent BETWEEN 0 AND 100",
            name="jobs_progress_range",
        ),
        sa.CheckConstraint(
            "error_category IS NULL OR error_category IN ('transient','deterministic')",
            name="jobs_error_category_valid",
        ),
    )

    # Job queue index — NOTE: no NOW() in partial index (volatile function not allowed).
    # next_retry_after is filtered at query time.
    op.create_index(
        "idx_jobs_queue",
        "jobs",
        ["priority", "created_at"],
        postgresql_ops={"priority": "DESC", "created_at": "ASC"},
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "idx_jobs_worker_status",
        "jobs",
        ["worker_id", "status"],
        postgresql_where=sa.text("status IN ('assigned', 'processing', 'paused')"),
    )
    op.create_index("idx_jobs_status", "jobs", ["status"])
    op.create_index(
        "idx_jobs_file_hash", "jobs", ["file_hash"],
        postgresql_where=sa.text("file_hash IS NOT NULL"),
    )
    op.create_index("idx_jobs_created_at", "jobs", [sa.text("created_at DESC")])
    op.create_index(
        "idx_jobs_completed_at",
        "jobs",
        [sa.text("completed_at DESC")],
        postgresql_where=sa.text("status = 'completed'"),
    )
    op.create_index("idx_jobs_input_path", "jobs", ["input_path"])

    # ── Add deferred FK: workers.current_job_id → jobs.id ────────────────────
    op.create_foreign_key(
        "fk_workers_current_job",
        "workers",
        "jobs",
        ["current_job_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── job_events ────────────────────────────────────────────────────────────
    op.create_table(
        "job_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_job_events_job_id", "job_events", ["job_id", "created_at"])
    op.create_index(
        "idx_job_events_worker_id",
        "job_events",
        ["worker_id", "created_at"],
        postgresql_where=sa.text("worker_id IS NOT NULL"),
    )
    op.create_index("idx_job_events_type", "job_events", ["event_type"])

    # ── worker_metrics ────────────────────────────────────────────────────────
    op.create_table(
        "worker_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recorded_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("cpu_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("memory_used_gb", sa.Numeric(6, 2), nullable=True),
        sa.Column("memory_total_gb", sa.Numeric(6, 2), nullable=True),
        sa.Column("memory_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("gpu_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("gpu_memory_used_gb", sa.Numeric(6, 2), nullable=True),
        sa.Column("current_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_progress_percent", sa.Numeric(5, 2), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["current_job_id"], ["jobs.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "idx_worker_metrics_worker_time",
        "worker_metrics",
        ["worker_id", "recorded_at"],
        postgresql_ops={"recorded_at": "DESC"},
    )
    op.create_index(
        "idx_worker_metrics_recorded_at",
        "worker_metrics",
        [sa.text("recorded_at DESC")],
    )

    # ── input_directories ─────────────────────────────────────────────────────
    op.create_table(
        "input_directories",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("output_path", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("watch_recursively", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("default_priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path"),
        sa.CheckConstraint("length(path) > 0", name="input_directories_path_nonempty"),
    )
    op.create_index(
        "idx_input_dirs_active",
        "input_directories",
        ["is_active"],
        postgresql_where=sa.text("is_active = true"),
    )

    # ── system_settings ───────────────────────────────────────────────────────
    op.create_table(
        "system_settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_table("input_directories")
    op.drop_table("worker_metrics")
    op.drop_table("job_events")
    op.drop_constraint("fk_workers_current_job", "workers", type_="foreignkey")
    op.drop_table("jobs")
    op.drop_table("workers")
