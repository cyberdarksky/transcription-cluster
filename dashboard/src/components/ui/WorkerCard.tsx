import { useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { useWSStore } from '@/store/websocket';
import { useToast } from '@/store/toasts';
import { api } from '@/lib/api';
import { fmtRTF, fmtSpeedup } from '@/lib/format';
import { asNumber } from '@/lib/normalize';
import { WorkerStatusBadge } from './Badge';
import { MetricBar } from './MetricBar';
import type { Worker } from '@/types';

interface WorkerCardProps {
  worker: Worker;
}

export function WorkerCard({ worker }: WorkerCardProps) {
  const qc = useQueryClient();
  const toast = useToast();

  const liveMetrics = useWSStore(s => s.workerMetrics[worker.id]);
  const liveProgress = useWSStore(s =>
    worker.current_job_id ? s.jobProgress[worker.current_job_id] : undefined
  );

  const cpu = liveMetrics?.cpu_percent ?? worker.last_cpu_percent;
  const mem = liveMetrics?.memory_percent ?? worker.last_memory_percent;
  const gpu = liveMetrics?.gpu_percent ?? worker.last_gpu_percent;
  const progress =
    asNumber(liveProgress?.progress_percent)
    ?? asNumber(liveMetrics?.current_job_progress)
    ?? asNumber(worker.current_job_progress)
    ?? null;

  const isOffline = worker.status === 'offline' || worker.status === 'error';
  const isBusy   = worker.status === 'busy';

  const pauseMut = useMutation({
    mutationFn: () =>
      worker.status === 'paused'
        ? api.workers.resume(worker.id)
        : api.workers.pause(worker.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workers'] });
      toast.success(
        worker.status === 'paused'
          ? `${worker.hostname} devam ettiriliyor`
          : `${worker.hostname} duraklatılıyor`,
      );
    },
    onError: () => {
      toast.error(`${worker.hostname} için işlem başarısız oldu`);
    },
  });

  // ── Status dot — use clsx properly (no string concatenation) ──────────────
  const dotClass = clsx(
    'status-dot flex-shrink-0',
    isOffline             ? 'bg-zinc-600'                  :
    worker.status === 'paused'  ? 'bg-amber-400'                 :
    worker.status === 'busy'    ? 'bg-emerald-400 animate-pulse' :
                                  'bg-emerald-400',
  );

  return (
    <article
      aria-label={`Worker: ${worker.hostname}`}
      className={clsx(
        'card flex flex-col animate-enter transition-opacity duration-300',
        isOffline && 'opacity-50',
      )}
    >
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="px-5 py-4 flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1">
            <div className={dotClass} aria-hidden="true" />
            <span className="font-semibold text-zinc-100 truncate">{worker.hostname}</span>
          </div>
          <p className="text-xs text-zinc-500 truncate">
            {[worker.cpu_model, worker.memory_total_gb ? `${worker.memory_total_gb} GB` : null]
              .filter(Boolean).join(' · ')}
          </p>
          <p className="text-xs text-zinc-600 font-mono">{worker.ip_address}</p>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <WorkerStatusBadge status={worker.status} />
          {!isOffline && (
            <button
              onClick={() => pauseMut.mutate()}
              disabled={pauseMut.isPending}
              aria-label={worker.status === 'paused' ? `${worker.hostname} işçisini devam ettir` : `${worker.hostname} işçisini duraklat`}
              className={clsx(
                'btn text-xs',
                worker.status === 'paused' ? 'btn-warning' : 'btn-ghost',
              )}
            >
              {pauseMut.isPending
                ? <span className="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" aria-hidden="true" />
                : worker.status === 'paused' ? 'Devam' : 'Duraklat'
              }
            </button>
          )}
        </div>
      </div>

      {/* ── Live metrics ─────────────────────────────────────────────────── */}
      {!isOffline && (
        <div className="px-5 pb-4 space-y-2.5">
          <MetricBar label="CPU"    value={cpu} />
          <MetricBar label="Bellek" value={mem} />
          {gpu != null && <MetricBar label="GPU" value={gpu} color="violet" />}
        </div>
      )}

      {/* ── Current job (busy workers only) ──────────────────────────────── */}
      {isBusy && worker.current_job_id && (
        <div className="px-5 pb-4 border-t border-zinc-800 pt-4 space-y-2">
          <p
            className="text-xs text-zinc-500 truncate font-mono"
            title={worker.current_job_path ?? undefined}
          >
            {worker.current_job_path?.split('/').pop() ?? 'İşleniyor…'}
          </p>
          <div aria-label={`İlerleme: ${progress?.toFixed(1) ?? 0}%`}>
            <div className="flex justify-between mb-1.5">
              <span className="text-xs text-zinc-500 tabular-nums">
                {progress != null ? `${progress.toFixed(1)}%` : 'Hesaplanıyor…'}
              </span>
              {/* ETA requires audio_duration — not shown without it */}
            </div>
            <div
              className="gauge-bar"
              role="progressbar"
              aria-valuenow={progress ?? 0}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="gauge-fill bg-gradient-to-r from-indigo-500 to-violet-400"
                style={{ width: `${progress ?? 0}%` }}
              />
            </div>
          </div>
        </div>
      )}

      {/* ── Footer stats — mt-auto ensures consistent card height ───────── */}
      <div className="mt-auto px-5 py-3 border-t border-zinc-800 flex items-center justify-between text-xs">
        <div className="flex items-center gap-4 text-zinc-500">
          <span className="flex items-center gap-1" aria-label={`${worker.jobs_completed} tamamlanan iş`}>
            <span className="text-emerald-500" aria-hidden="true">✓</span>
            <span className="tabular-nums">{worker.jobs_completed.toLocaleString('tr')}</span>
          </span>
          {worker.jobs_failed > 0 && (
            <span className="flex items-center gap-1" aria-label={`${worker.jobs_failed} başarısız iş`}>
              <span className="text-rose-500" aria-hidden="true">✗</span>
              <span className="tabular-nums">{worker.jobs_failed}</span>
            </span>
          )}
          {worker.total_audio_hours > 0 && (
            <span className="text-zinc-600">
              {worker.total_audio_hours.toFixed(1)} sa
            </span>
          )}
        </div>

        {worker.average_rtf != null && (
          <span className="text-zinc-500 font-mono" aria-label={`Ortalama RTF: ${fmtRTF(worker.average_rtf)}`}>
            {fmtRTF(worker.average_rtf)}
            <span className="text-zinc-600 ml-1">({fmtSpeedup(worker.average_rtf)})</span>
          </span>
        )}
      </div>
    </article>
  );
}
