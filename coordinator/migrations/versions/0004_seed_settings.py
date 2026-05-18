"""Seed default system settings

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-18

"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SETTINGS = [
    ("worker_heartbeat_timeout_seconds", "90",
     "Bu süre (sn) sonra kalp atışı gelmezse işçi offline sayılır"),
    ("max_retries_default", "3",
     "Her iş için varsayılan maksimum yeniden deneme sayısı"),
    ("retry_delay_seconds", "[0, 60, 300]",
     "Yeniden deneme gecikmelerinin JSON dizisi (saniye)"),
    ("worker_metrics_retention_days", "7",
     "İşçi metrik saklama süresi (gün)"),
    ("job_events_retention_days", "90",
     "İş olayı saklama süresi (gün)"),
    ("max_concurrent_jobs_per_worker", "1",
     "İşçi başına maksimum eş zamanlı iş (her zaman 1 olmalı)"),
    ("dashboard_refresh_interval_ms", "5000",
     "Dashboard WebSocket kalp atışı aralığı (ms)"),
    ("file_watcher_debounce_seconds", "2",
     "Yeni dosyalar algılamadan önce dosya istikrarı bekleme süresi"),
    ("whisper_model", '"mlx-community/whisper-medium-mlx"',
     "Kullanılan Whisper model tanımlayıcısı (yerel yol veya HF repo)"),
    ("whisper_language", '"tr"',
     "Transkripsiyon dili kodu"),
    ("whisper_word_timestamps", "true",
     "Kelime düzeyinde zaman damgası etkinleştirme"),
    ("job_timeout_multiplier", "5",
     "max_job_duration = audio_duration * bu_katsayı (sn). Sonsuz döngü koruması"),
    ("coordinator_recovery_grace_seconds", "30",
     "Koordinatör yeniden başlatmasından sonra işçilerin yeniden bağlanması için bekleme süresi"),
]


def upgrade() -> None:
    for key, value, description in SETTINGS:
        op.execute(
            f"""
            INSERT INTO system_settings (key, value, description)
            VALUES ('{key}', '{value}'::jsonb, '{description}')
            ON CONFLICT (key) DO NOTHING;
            """
        )


def downgrade() -> None:
    keys = ", ".join(f"'{k}'" for k, _, _ in SETTINGS)
    op.execute(f"DELETE FROM system_settings WHERE key IN ({keys})")
