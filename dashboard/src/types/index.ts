// ── Job types ──────────────────────────────────────────────────────────────

export type JobStatus =
  | 'queued' | 'assigned' | 'downloading' | 'processing'
  | 'uploading' | 'completed' | 'failed' | 'paused'
  | 'retry_wait' | 'cancelled';

export interface Job {
  id: string;
  input_path: string;
  original_filename: string;
  relative_folder: string;
  status: JobStatus;
  priority: number;
  retry_count: number;
  max_retries: number;
  progress_percent: number | null;
  file_size_bytes: number | null;
  worker_id: string | null;
  worker_hostname: string | null;
  created_at: string;
  assigned_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  audio_duration_seconds: number | null;
  processing_time_seconds: number | null;
  rtf: number | null;
  last_error: string | null;
  error_category: 'transient' | 'deterministic' | null;
}

export interface JobEvent {
  id: number;
  job_id: string;
  worker_id: string | null;
  event_type: string;
  details: Record<string, unknown> | null;
  created_at: string;
}

export interface PaginatedJobs {
  items: Job[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

// ── Worker types ───────────────────────────────────────────────────────────

export type WorkerStatus = 'online' | 'idle' | 'busy' | 'paused' | 'offline' | 'error';

export interface Worker {
  id: string;
  stable_worker_id: string | null;
  hostname: string;
  ip_address: string;
  status: WorkerStatus;
  cpu_model: string | null;
  cpu_cores: number | null;
  memory_total_gb: number | null;
  gpu_model: string | null;
  whisper_backend: string;
  worker_version: string | null;
  last_heartbeat: string | null;
  current_job_id: string | null;
  current_job_path: string | null;
  current_job_progress: number | null;
  jobs_completed: number;
  jobs_failed: number;
  total_audio_hours: number;
  average_rtf: number | null;
  last_cpu_percent: number | null;
  last_memory_percent: number | null;
  last_gpu_percent: number | null;
  registered_at: string;
}

export interface WorkerMetricPoint {
  time: string;
  cpu_percent: number | null;
  memory_percent: number | null;
  gpu_percent: number | null;
  job_progress: number | null;
}

// ── System types ───────────────────────────────────────────────────────────

export interface SystemStats {
  jobs: {
    total: number; pending: number; assigned: number;
    downloading: number; processing: number; uploading: number;
    paused: number; completed: number;
    failed: number; cancelled: number;
  };
  workers: {
    total: number; online: number; offline: number; busy: number; idle: number;
  };
  throughput: {
    jobs_completed_last_1h: number;
    jobs_completed_last_24h: number;
    audio_hours_last_24h: number;
    avg_rtf_last_24h: number | null;
  };
  coordinator: {
    version: string;
    uptime_seconds: number;
    db_connected: boolean;
    storage_used_gb: number | null;
    storage_available_gb: number | null;
  };
}

export interface SystemSettings {
  worker_heartbeat_timeout_seconds: number;
  max_retries_default: number;
  retry_delay_seconds: number[];
  worker_metrics_retention_days: number;
  job_events_retention_days: number;
  dashboard_refresh_interval_ms: number;
  file_watcher_debounce_seconds?: number;
  whisper_model: string;
  whisper_language: string;
  whisper_word_timestamps?: boolean;
  job_timeout_multiplier: number;
  coordinator_recovery_grace_seconds: number;
}

export interface InputDirectory {
  id: string;
  path: string;
  output_path: string;
  is_active: boolean;
  watch_recursively: boolean;
  default_priority: number;
  label: string | null;
  created_at: string;
}

// ── WebSocket event types ─────────────────────────────────────────────────

export type WSEventType =
  | 'connected' | 'heartbeat' | 'initial_state'
  | 'job_created' | 'job_status_changed' | 'job_progress'
  | 'worker_metrics' | 'worker_status_changed'
  | 'system_alert' | 'pong';

export interface WSMessage {
  type: WSEventType;
  data?: Record<string, unknown>;
  coordinator_version?: string;
}

// ── UI types ───────────────────────────────────────────────────────────────

export interface Alert {
  id: string;
  severity: 'info' | 'warning' | 'error';
  code: string;
  message: string;
  timestamp: string;
}

export interface LogEntry {
  id: string;
  timestamp: string;
  type: string;
  level: 'info' | 'warning' | 'error' | 'success';
  message: string;
  details?: Record<string, unknown>;
}
