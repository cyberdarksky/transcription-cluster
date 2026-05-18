"""Triggers and database functions

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-18

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── updated_at auto-update function ───────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION trigger_set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    for table in ("workers", "jobs", "input_directories", "system_settings"):
        op.execute(f"""
            CREATE TRIGGER set_{table}_updated_at
                BEFORE UPDATE ON {table}
                FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
        """)

    # ── Job status change event logger ────────────────────────────────────────
    # Only fires on status column changes (AFTER UPDATE OF status).
    # Does NOT fire on progress_percent updates — avoiding write amplification.
    op.execute("""
        CREATE OR REPLACE FUNCTION trigger_log_job_status_change()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.status = NEW.status THEN
                RETURN NEW;
            END IF;

            INSERT INTO job_events (job_id, worker_id, event_type, details)
            VALUES (
                NEW.id,
                NEW.worker_id,
                NEW.status,
                jsonb_build_object(
                    'previous_status', OLD.status,
                    'new_status',      NEW.status,
                    'retry_count',     NEW.retry_count
                )
            );

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER log_job_status_change
            AFTER UPDATE OF status ON jobs
            FOR EACH ROW EXECUTE FUNCTION trigger_log_job_status_change();
    """)

    # ── recover_stale_jobs() ──────────────────────────────────────────────────
    # Called by the coordinator after the grace period on restart.
    # Re-queues jobs owned by OFFLINE workers.
    op.execute("""
        CREATE OR REPLACE FUNCTION recover_stale_jobs()
        RETURNS INTEGER AS $$
        DECLARE
            recovered_count INTEGER;
        BEGIN
            WITH stale_jobs AS (
                UPDATE jobs j
                SET
                    status           = 'pending',
                    worker_id        = NULL,
                    assigned_at      = NULL,
                    started_at       = NULL,
                    paused_at        = NULL,
                    progress_percent = NULL,
                    next_retry_after = CASE
                        WHEN j.retry_count = 0 THEN NULL
                        WHEN j.retry_count = 1 THEN NOW() + INTERVAL '60 seconds'
                        ELSE                       NOW() + INTERVAL '300 seconds'
                    END,
                    retry_count      = j.retry_count + 1,
                    last_error       = 'İşçi bağlantı kesilmesi — iş yeniden kuyruğa alındı',
                    updated_at       = NOW()
                FROM workers w
                WHERE j.worker_id = w.id
                  AND w.status IN ('offline', 'error')
                  AND j.status IN ('assigned', 'processing', 'paused')
                  AND j.retry_count < j.max_retries
                RETURNING j.id
            )
            SELECT COUNT(*) INTO recovered_count FROM stale_jobs;

            UPDATE jobs j
            SET
                status     = 'failed',
                last_error = 'İşçi bağlantı kesilmesi nedeniyle maksimum yeniden deneme sayısına ulaşıldı',
                updated_at = NOW()
            FROM workers w
            WHERE j.worker_id = w.id
              AND w.status IN ('offline', 'error')
              AND j.status IN ('assigned', 'processing', 'paused')
              AND j.retry_count >= j.max_retries;

            RETURN COALESCE(recovered_count, 0);
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── cleanup_old_metrics() ─────────────────────────────────────────────────
    # Called by the FastAPI daily maintenance task.
    op.execute("""
        CREATE OR REPLACE FUNCTION cleanup_old_metrics(
            metrics_days INTEGER DEFAULT 7,
            events_days  INTEGER DEFAULT 90
        )
        RETURNS void AS $$
        BEGIN
            DELETE FROM worker_metrics
            WHERE recorded_at < NOW() - (metrics_days || ' days')::INTERVAL;

            -- Keep 'completed' events forever; remove old non-essential ones
            DELETE FROM job_events
            WHERE event_type != 'completed'
              AND created_at < NOW() - (events_days || ' days')::INTERVAL;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS cleanup_old_metrics(INTEGER, INTEGER)")
    op.execute("DROP FUNCTION IF EXISTS recover_stale_jobs()")
    op.execute("DROP TRIGGER IF EXISTS log_job_status_change ON jobs")
    op.execute("DROP FUNCTION IF EXISTS trigger_log_job_status_change()")
    for table in ("system_settings", "input_directories", "jobs", "workers"):
        op.execute(f"DROP TRIGGER IF EXISTS set_{table}_updated_at ON {table}")
    op.execute("DROP FUNCTION IF EXISTS trigger_set_updated_at()")
