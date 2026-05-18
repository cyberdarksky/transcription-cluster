"""Database views for dashboard queries

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-18

"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE VIEW v_dashboard_summary AS
        SELECT
            COUNT(*) FILTER (WHERE status = 'pending')    AS jobs_pending,
            COUNT(*) FILTER (WHERE status = 'processing') AS jobs_processing,
            COUNT(*) FILTER (WHERE status = 'paused')     AS jobs_paused,
            COUNT(*) FILTER (WHERE status = 'completed')  AS jobs_completed,
            COUNT(*) FILTER (WHERE status = 'failed')     AS jobs_failed,
            COUNT(*) FILTER (WHERE status = 'cancelled')  AS jobs_cancelled,
            COUNT(*)                                       AS jobs_total,
            ROUND(
                SUM(audio_duration_seconds)
                    FILTER (WHERE status = 'completed') / 3600.0, 2
            ) AS total_audio_hours_completed,
            ROUND(AVG(rtf) FILTER (WHERE status = 'completed' AND rtf IS NOT NULL), 4)
                AS avg_rtf,
            COUNT(*) FILTER (
                WHERE status = 'completed' AND completed_at > NOW() - INTERVAL '24h'
            ) AS jobs_completed_last_24h
        FROM jobs;
    """)

    op.execute("""
        CREATE OR REPLACE VIEW v_worker_status AS
        SELECT
            w.id,
            w.hostname,
            w.ip_address,
            w.status,
            w.cpu_model,
            w.cpu_cores,
            w.memory_total_gb,
            w.gpu_model,
            w.last_heartbeat,
            EXTRACT(EPOCH FROM (NOW() - w.last_heartbeat))::INTEGER AS seconds_since_heartbeat,
            w.current_job_id,
            j.input_path          AS current_job_path,
            j.progress_percent    AS current_job_progress,
            w.jobs_completed,
            w.jobs_failed,
            ROUND(w.total_audio_seconds / 3600.0, 2) AS total_audio_hours,
            w.average_rtf,
            wm.cpu_percent        AS last_cpu_percent,
            wm.memory_percent     AS last_memory_percent,
            wm.gpu_percent        AS last_gpu_percent
        FROM workers w
        LEFT JOIN jobs j ON w.current_job_id = j.id
        LEFT JOIN LATERAL (
            SELECT cpu_percent, memory_percent, gpu_percent
            FROM worker_metrics
            WHERE worker_id = w.id
            ORDER BY recorded_at DESC
            LIMIT 1
        ) wm ON true
        ORDER BY w.status DESC, w.hostname;
    """)

    op.execute("""
        CREATE OR REPLACE VIEW v_job_queue AS
        SELECT
            j.id,
            j.input_path,
            j.original_filename,
            j.relative_folder,
            j.status,
            j.priority,
            j.retry_count,
            j.max_retries,
            j.progress_percent,
            j.file_size_bytes,
            w.hostname               AS assigned_worker_hostname,
            j.created_at,
            j.assigned_at,
            j.started_at,
            j.completed_at,
            CASE
                WHEN j.status = 'processing' AND j.started_at IS NOT NULL
                THEN EXTRACT(EPOCH FROM (NOW() - j.started_at))::INTEGER
                ELSE NULL
            END AS elapsed_seconds,
            j.audio_duration_seconds,
            j.processing_time_seconds,
            j.rtf,
            j.last_error,
            j.error_category
        FROM jobs j
        LEFT JOIN workers w ON j.worker_id = w.id
        ORDER BY
            CASE j.status
                WHEN 'processing' THEN 1
                WHEN 'paused'     THEN 2
                WHEN 'assigned'   THEN 3
                WHEN 'pending'    THEN 4
                WHEN 'failed'     THEN 5
                WHEN 'completed'  THEN 6
                WHEN 'cancelled'  THEN 7
                ELSE 8
            END,
            j.priority DESC,
            j.created_at ASC;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_job_queue")
    op.execute("DROP VIEW IF EXISTS v_worker_status")
    op.execute("DROP VIEW IF EXISTS v_dashboard_summary")
