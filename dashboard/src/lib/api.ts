import type {
  Job, Worker, WorkerMetricPoint, SystemStats,
  SystemSettings, InputDirectory, PaginatedJobs,
} from '@/types';
import { asInt, asNumber } from '@/lib/normalize';

const BASE = '/api/v1';

function normalizeJob(raw: Record<string, unknown>): Job {
  return {
    ...(raw as unknown as Job),
    priority: asInt(raw.priority),
    retry_count: asInt(raw.retry_count),
    max_retries: asInt(raw.max_retries, 3),
    progress_percent: asNumber(raw.progress_percent),
    file_size_bytes: asNumber(raw.file_size_bytes),
    audio_duration_seconds: asNumber(raw.audio_duration_seconds),
    processing_time_seconds: asNumber(raw.processing_time_seconds),
    rtf: asNumber(raw.rtf),
  };
}

function normalizeWorker(raw: Record<string, unknown>): Worker {
  return {
    ...(raw as unknown as Worker),
    cpu_cores: asNumber(raw.cpu_cores),
    memory_total_gb: asNumber(raw.memory_total_gb),
    jobs_completed: asInt(raw.jobs_completed),
    jobs_failed: asInt(raw.jobs_failed),
    total_audio_hours: asNumber(raw.total_audio_hours) ?? 0,
    average_rtf: asNumber(raw.average_rtf),
    current_job_progress: asNumber(raw.current_job_progress),
    last_cpu_percent: asNumber(raw.last_cpu_percent),
    last_memory_percent: asNumber(raw.last_memory_percent),
    last_gpu_percent: asNumber(raw.last_gpu_percent),
  };
}

function normalizeStats(raw: SystemStats): SystemStats {
  return {
    ...raw,
    throughput: {
      ...raw.throughput,
      audio_hours_last_24h: asNumber(raw.throughput.audio_hours_last_24h) ?? 0,
      avg_rtf_last_24h: asNumber(raw.throughput.avg_rtf_last_24h),
    },
    coordinator: {
      ...raw.coordinator,
      uptime_seconds: asNumber(raw.coordinator.uptime_seconds) ?? 0,
      storage_used_gb: asNumber(raw.coordinator.storage_used_gb),
      storage_available_gb: asNumber(raw.coordinator.storage_available_gb),
    },
  };
}

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ── Jobs ───────────────────────────────────────────────────────────────────

export const api = {
  jobs: {
    list: (params: {
      status?: string[];
      folder?: string;
      filename?: string;
      sort?: string;
      page?: number;
      page_size?: number;
    } = {}): Promise<PaginatedJobs> => {
      const q = new URLSearchParams();
      params.status?.forEach(s => q.append('status', s));
      if (params.folder) q.set('folder', params.folder);
      if (params.filename) q.set('filename', params.filename);
      if (params.sort) q.set('sort', params.sort);
      if (params.page) q.set('page', String(params.page));
      if (params.page_size) q.set('page_size', String(params.page_size));
      return req<PaginatedJobs>(`/jobs?${q}`).then(page => ({
        ...page,
        items: page.items.map(j => normalizeJob(j as unknown as Record<string, unknown>)),
      }));
    },

    get: (id: string): Promise<Job> =>
      req<Record<string, unknown>>(`/jobs/${id}`).then(normalizeJob),

    pause: (id: string) =>
      req(`/jobs/${id}/pause`, { method: 'POST' }),

    resume: (id: string) =>
      req(`/jobs/${id}/resume`, { method: 'POST' }),

    cancel: (id: string) =>
      req(`/jobs/${id}/cancel`, { method: 'POST' }),

    retry: (id: string) =>
      req(`/jobs/${id}/retry`, { method: 'POST' }),

    downloadSrt: (id: string) =>
      `${BASE}/jobs/${id}/output/srt`,

    downloadJson: (id: string) =>
      `${BASE}/jobs/${id}/output/json`,
  },

  workers: {
    list: (): Promise<{ items: Worker[]; total: number }> =>
      req<{ items: Record<string, unknown>[]; total: number }>('/workers').then(r => ({
        ...r,
        items: r.items.map(normalizeWorker),
      })),

    get: (id: string): Promise<Worker> =>
      req<Record<string, unknown>>(`/workers/${id}`).then(normalizeWorker),

    metrics: (id: string, from?: string): Promise<{ metrics: WorkerMetricPoint[] }> =>
      req(`/workers/${id}/metrics${from ? `?from=${from}` : ''}`),

    pause: (id: string) =>
      req(`/workers/${id}/pause`, { method: 'POST' }),

    resume: (id: string) =>
      req(`/workers/${id}/resume`, { method: 'POST' }),
  },

  system: {
    stats: (): Promise<SystemStats> =>
      req<SystemStats>('/system/stats').then(normalizeStats),

    settings: (): Promise<SystemSettings> =>
      req('/system/settings'),

    updateSettings: (data: Partial<SystemSettings>): Promise<SystemSettings> =>
      req('/system/settings', { method: 'PUT', body: JSON.stringify(data) }),

    inputDirs: (): Promise<InputDirectory[]> =>
      req('/system/input-directories'),

    addInputDir: (data: { path: string; output_path: string; label?: string }) =>
      req('/system/input-directories', { method: 'POST', body: JSON.stringify(data) }),

    deleteInputDir: (id: string) =>
      req(`/system/input-directories/${id}`, { method: 'DELETE' }),

    scan: (input_directory_id?: string) =>
      req('/system/scan', {
        method: 'POST',
        body: JSON.stringify({ input_directory_id }),
      }),
  },
} as const;
