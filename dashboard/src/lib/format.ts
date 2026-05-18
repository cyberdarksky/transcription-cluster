import type { JobStatus, WorkerStatus } from '@/types';

// ── Duration ───────────────────────────────────────────────────────────────

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || isNaN(seconds)) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${pad(m)}:${pad(s)}`;
  if (m > 0) return `${m}:${pad(s)}`;
  return `${s}s`;
}

function pad(n: number) { return String(n).padStart(2, '0'); }

// ── ETA ───────────────────────────────────────────────────────────────────

export function fmtETA(
  audioDurationSeconds: number | null,
  progressPercent: number | null,
  avgRTF = 0.38,
): string {
  if (!audioDurationSeconds || !progressPercent || progressPercent <= 0) return '';
  if (progressPercent >= 99) return 'Neredeyse bitti';
  const totalEstimate = audioDurationSeconds * avgRTF;
  const elapsed = (progressPercent / 100) * totalEstimate;
  const remaining = totalEstimate - elapsed;
  if (remaining < 60) return `~ ${Math.round(remaining)} sn`;
  if (remaining < 3600) return `~ ${Math.round(remaining / 60)} dk`;
  return `~ ${(remaining / 3600).toFixed(1)} sa`;
}

// ── Bytes ──────────────────────────────────────────────────────────────────

export function fmtBytes(bytes: number | null | undefined): string {
  if (bytes == null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

// ── RTF ───────────────────────────────────────────────────────────────────

export function fmtRTF(rtf: number | null | undefined): string {
  if (rtf == null) return '—';
  return `${rtf.toFixed(3)}`;
}

export function fmtSpeedup(rtf: number | null | undefined): string {
  if (rtf == null || rtf <= 0) return '';
  return `${(1 / rtf).toFixed(1)}×`;
}

// ── Relative time ─────────────────────────────────────────────────────────

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return '—';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return 'az önce';
  if (diff < 3600) return `${Math.round(diff / 60)} dk önce`;
  if (diff < 86400) return `${Math.round(diff / 3600)} sa önce`;
  return `${Math.round(diff / 86400)} gün önce`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('tr-TR', {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('tr-TR', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

// ── Status labels ─────────────────────────────────────────────────────────

const JOB_STATUS_TR: Record<JobStatus, string> = {
  queued: 'Bekliyor',
  assigned: 'Atandı',
  downloading: 'İndiriliyor',
  processing: 'İşleniyor',
  uploading: 'Yükleniyor',
  completed: 'Tamamlandı',
  failed: 'Başarısız',
  paused: 'Duraklatıldı',
  retry_wait: 'Yeniden Deneme Bekliyor',
  cancelled: 'İptal Edildi',
};

const WORKER_STATUS_TR: Record<WorkerStatus, string> = {
  online: 'Çevrimiçi',
  idle: 'Boşta',
  busy: 'Meşgul',
  paused: 'Duraklatıldı',
  offline: 'Çevrimdışı',
  error: 'Hata',
};

export function fmtJobStatus(s: JobStatus): string {
  return JOB_STATUS_TR[s] ?? s;
}

export function fmtWorkerStatus(s: WorkerStatus): string {
  return WORKER_STATUS_TR[s] ?? s;
}

// ── Percent ───────────────────────────────────────────────────────────────

export function fmtPercent(n: number | null | undefined, decimals = 1): string {
  if (n == null) return '—';
  return `${n.toFixed(decimals)}%`;
}

// ── Uptime ────────────────────────────────────────────────────────────────

export function fmtUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}g ${h}sa ${m}dk`;
  if (h > 0) return `${h}sa ${m}dk`;
  return `${m}dk`;
}
