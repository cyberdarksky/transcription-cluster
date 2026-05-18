"""Distributed queue: new states, lease columns, updated constraints and indexes

Adds granular job states (downloading, uploading, retry_wait), migrates
legacy 'pending' → 'queued', and adds lease columns for the lease-based
worker assignment system.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-18

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Widen the CHECK constraint to include all new states ───────────────
    # Temporarily allow both old and new values during migration.
    op.drop_constraint("jobs_status_valid", "jobs", type_="check")
    op.create_check_constraint(
        "jobs_status_valid",
        "jobs",
        """status IN (
            'queued', 'assigned', 'downloading', 'processing',
            'uploading', 'completed', 'failed', 'paused',
            'retry_wait', 'cancelled',
            'pending'
        )""",
    )

    # ── 2. Migrate legacy 'pending' → 'queued' ────────────────────────────────
    op.execute("UPDATE jobs SET status='queued' WHERE status='pending'")

    # ── 3. Drop 'pending' from constraint (no more pending rows) ─────────────
    op.drop_constraint("jobs_status_valid", "jobs", type_="check")
    op.create_check_constraint(
        "jobs_status_valid",
        "jobs",
        """status IN (
            'queued', 'assigned', 'downloading', 'processing',
            'uploading', 'completed', 'failed', 'paused',
            'retry_wait', 'cancelled'
        )""",
    )

    # ── 4. Add lease columns ──────────────────────────────────────────────────
    op.add_column("jobs", sa.Column(
        "lease_expires_at",
        sa.TIMESTAMP(timezone=True),
        nullable=True,
    ))
    op.add_column("jobs", sa.Column(
        "lease_renewed_count",
        sa.Integer(),
        nullable=False,
        server_default="0",
    ))

    # ── 5. Index for lease recovery queries ───────────────────────────────────
    # Recovery service: "find jobs where lease expired AND status is active"
    # idx_jobs_queue must be updated to use 'queued' instead of 'pending'.
    # The original index was created with WHERE status='pending'; we recreate it.
    op.drop_index("idx_jobs_queue", table_name="jobs", if_exists=True)
    op.create_index(
        "idx_jobs_queue",
        "jobs",
        ["priority", "created_at"],
        postgresql_ops={"priority": "DESC", "created_at": "ASC"},
        postgresql_where=sa.text("status = 'queued'"),
    )

    # Lease expiry scan — used by LeaseRecoveryService every 30 seconds
    op.create_index(
        "idx_jobs_lease_expiry",
        "jobs",
        ["lease_expires_at"],
        postgresql_where=sa.text(
            "status IN ('assigned','downloading','processing','uploading')"
            " AND lease_expires_at IS NOT NULL"
        ),
    )

    # retry_wait scan — used by RetryScheduler every 30 seconds
    op.create_index(
        "idx_jobs_retry_wait",
        "jobs",
        ["next_retry_after"],
        postgresql_where=sa.text(
            "status = 'retry_wait' AND next_retry_after IS NOT NULL"
        ),
    )

    # ── 6. Update job_events event_type constraint (add new types) ────────────
    # The job_events table has no check constraint on event_type (it's free-form),
    # so no DDL change is needed. New event types are just new strings.

    # ── 7. Update system_settings seeds for new settings ─────────────────────
    new_settings = [
        ("job_lease_duration_seconds",       "300",  "İş kira süresi (saniye); işçi bu süre içinde yenilemezse görev kurtarılır"),
        ("lease_recovery_interval_seconds",  "30",   "Süresi dolmuş kiralara ne sıklıkla bakılır (saniye)"),
        ("retry_scheduler_interval_seconds", "30",   "retry_wait işlerin queued'a geçişi için kontrol aralığı (saniye)"),
    ]
    for key, value, description in new_settings:
        op.execute(
            f"INSERT INTO system_settings (key, value, description) "
            f"VALUES ('{key}', '{value}'::jsonb, '{description}') "
            f"ON CONFLICT (key) DO NOTHING"
        )


def downgrade() -> None:
    op.drop_index("idx_jobs_retry_wait", table_name="jobs", if_exists=True)
    op.drop_index("idx_jobs_lease_expiry", table_name="jobs", if_exists=True)

    # Restore old queue index pointing at 'pending'
    op.drop_index("idx_jobs_queue", table_name="jobs", if_exists=True)
    op.create_index(
        "idx_jobs_queue",
        "jobs",
        ["priority", "created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.drop_column("jobs", "lease_renewed_count")
    op.drop_column("jobs", "lease_expires_at")

    # Restore all 'queued' rows back to 'pending'
    op.drop_constraint("jobs_status_valid", "jobs", type_="check")
    op.create_check_constraint(
        "jobs_status_valid",
        "jobs",
        """status IN (
            'queued','assigned','downloading','processing',
            'uploading','completed','failed','paused',
            'retry_wait','cancelled','pending'
        )""",
    )
    op.execute("UPDATE jobs SET status='pending' WHERE status='queued'")
    op.drop_constraint("jobs_status_valid", "jobs", type_="check")
    op.create_check_constraint(
        "jobs_status_valid",
        "jobs",
        """status IN (
            'pending','assigned','processing','paused',
            'completed','failed','cancelled'
        )""",
    )
