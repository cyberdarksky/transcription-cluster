import { create } from 'zustand';
import type { Worker, Alert, LogEntry } from '@/types';

interface LiveWorkerMetrics {
  cpu_percent: number | null;
  memory_percent: number | null;
  gpu_percent: number | null;
  current_job_progress: number | null;
  timestamp: string;
}

interface LiveJobProgress {
  progress_percent: number;
  elapsed_seconds: number | null;
  worker_id: string | null;
}

export interface WSStore {
  connected: boolean;
  coordinatorVersion: string;

  // Live worker metrics (updated every heartbeat ~30s)
  workerMetrics: Record<string, LiveWorkerMetrics>;

  // Live job progress (updated every ~10s)
  jobProgress: Record<string, LiveJobProgress>;

  // Recent status changes (for log viewer)
  alerts: Alert[];
  logs: LogEntry[];

  // Job counts (quick stats from status-change events)
  invalidatedAt: number; // bump to trigger query refetch

  // Actions
  setConnected: (v: boolean) => void;
  handleMessage: (msg: { type: string; data?: Record<string, unknown>; coordinator_version?: string }) => void;
  clearLogs: () => void;
}

let _logId = 0;

export const useWSStore = create<WSStore>((set, get) => ({
  connected: false,
  coordinatorVersion: '',
  workerMetrics: {},
  jobProgress: {},
  alerts: [],
  logs: [],
  invalidatedAt: 0,

  setConnected: (v) => set({ connected: v }),

  clearLogs: () => set({ logs: [] }),

  handleMessage: (msg) => {
    const { type, data } = msg;
    const now = new Date().toISOString();

    set((state) => {
      const updates: Partial<WSStore> = {};

      // ── WebSocket message handlers ──────────────────────────────────────

      if (type === 'connected') {
        updates.coordinatorVersion = msg.coordinator_version ?? '';
      }

      if (type === 'worker_metrics' && data) {
        updates.workerMetrics = {
          ...state.workerMetrics,
          [data.worker_id as string]: {
            cpu_percent: data.cpu_percent as number | null,
            memory_percent: data.memory_percent as number | null,
            gpu_percent: data.gpu_percent as number | null,
            current_job_progress: data.current_job_progress as number | null,
            timestamp: data.timestamp as string ?? now,
          },
        };
      }

      if (type === 'job_progress' && data) {
        updates.jobProgress = {
          ...state.jobProgress,
          [data.job_id as string]: {
            progress_percent: data.progress_percent as number,
            elapsed_seconds: data.elapsed_seconds as number | null,
            worker_id: data.worker_id as string | null,
          },
        };
      }

      if (type === 'job_status_changed' || type === 'job_created' || type === 'worker_status_changed') {
        // Trigger query invalidation
        updates.invalidatedAt = Date.now();

        // Add to log
        const entry = buildLogEntry(type, data, now);
        updates.logs = [entry, ...state.logs].slice(0, 500);
      }

      if (type === 'system_alert' && data) {
        const alert: Alert = {
          id: String(++_logId),
          severity: (data.severity as Alert['severity']) ?? 'info',
          code: String(data.code ?? ''),
          message: String(data.message ?? ''),
          timestamp: String(data.timestamp ?? now),
        };
        updates.alerts = [alert, ...state.alerts].slice(0, 100);

        // Also add to log
        const entry: LogEntry = {
          id: String(_logId),
          timestamp: alert.timestamp,
          type: 'system_alert',
          level: alert.severity === 'error' ? 'error' : alert.severity === 'warning' ? 'warning' : 'info',
          message: `[${alert.code}] ${alert.message}`,
          details: data,
        };
        updates.logs = [entry, ...(updates.logs ?? state.logs)].slice(0, 500);
      }

      return updates;
    });
  },
}));

function buildLogEntry(
  type: string,
  data: Record<string, unknown> | undefined,
  now: string,
): LogEntry {
  let level: LogEntry['level'] = 'info';
  let message = '';

  if (type === 'job_status_changed' && data) {
    const ns = String(data.new_status ?? '');
    const ps = String(data.previous_status ?? '');
    const path = String(data.input_path ?? data.job_id ?? '');
    level = ns === 'failed' ? 'error' : ns === 'completed' ? 'success' : 'info';
    message = `İş: ${ps} → ${ns}  ${path ? `(${path.split('/').pop()})` : ''}`;
  } else if (type === 'job_created' && data) {
    message = `Yeni iş oluşturuldu: ${String(data.input_path ?? '').split('/').pop()}`;
  } else if (type === 'worker_status_changed' && data) {
    const ns = String(data.new_status ?? '');
    level = ns === 'offline' ? 'warning' : 'info';
    message = `İşçi ${String(data.hostname ?? '')}: ${String(data.previous_status ?? '')} → ${ns}`;
  }

  return {
    id: String(++_logId),
    timestamp: String(data?.timestamp ?? now),
    type,
    level,
    message,
    details: data,
  };
}
