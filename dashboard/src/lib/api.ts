import type {
  Job, Worker, WorkerMetricPoint, SystemStats,
  SystemSettings, InputDirectory, PaginatedJobs,
} from '@/types';

const BASE = '/api/v1';

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
      return req(`/jobs?${q}`);
    },

    get: (id: string): Promise<Job> =>
      req(`/jobs/${id}`),

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
      req('/workers'),

    get: (id: string): Promise<Worker> =>
      req(`/workers/${id}`),

    metrics: (id: string, from?: string): Promise<{ metrics: WorkerMetricPoint[] }> =>
      req(`/workers/${id}/metrics${from ? `?from=${from}` : ''}`),

    pause: (id: string) =>
      req(`/workers/${id}/pause`, { method: 'POST' }),

    resume: (id: string) =>
      req(`/workers/${id}/resume`, { method: 'POST' }),
  },

  system: {
    stats: (): Promise<SystemStats> =>
      req('/system/stats'),

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
