"""Align recover_stale_jobs() with v2 job statuses (queued, retry_wait).

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-18

"""
from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION recover_stale_jobs()
        RETURNS INTEGER AS $$
        DECLARE
            recovered_count INTEGER;
        BEGIN
            WITH stale_jobs AS (
                UPDATE jobs j
                SET
                    status           = 'retry_wait',
                    worker_id        = NULL,
                    lease_expires_at = NULL,
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
                    error_category   = 'transient',
                    last_error       = 'İşçi bağlantı kesilmesi — iş yeniden kuyruğa alındı',
                    updated_at       = NOW()
                FROM workers w
                WHERE j.worker_id = w.id
                  AND w.status IN ('offline', 'error')
                  AND j.status IN (
                      'assigned', 'downloading', 'processing',
                      'uploading', 'paused'
                  )
                  AND j.retry_count < j.max_retries
                  AND (j.error_category IS NULL OR j.error_category != 'deterministic')
                RETURNING j.id
            )
            SELECT COUNT(*) INTO recovered_count FROM stale_jobs;

            UPDATE jobs j
            SET
                status     = 'failed',
                worker_id  = NULL,
                lease_expires_at = NULL,
                last_error = 'İşçi bağlantı kesilmesi nedeniyle maksimum yeniden deneme sayısına ulaşıldı',
                updated_at = NOW()
            FROM workers w
            WHERE j.worker_id = w.id
              AND w.status IN ('offline', 'error')
              AND j.status IN (
                  'assigned', 'downloading', 'processing',
                  'uploading', 'paused'
              )
              AND (
                  j.retry_count >= j.max_retries
                  OR j.error_category = 'deterministic'
              );

            UPDATE workers w
            SET current_job_id = NULL, updated_at = NOW()
            WHERE w.status IN ('offline', 'error')
              AND w.current_job_id IS NOT NULL;

            RETURN COALESCE(recovered_count, 0);
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    # Revert to 0002 definition (legacy pending status).
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
