import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { api } from '@/lib/api';
import { fmtDuration, fmtDateTime, fmtETA, fmtRTF } from '@/lib/format';
import { asNumber } from '@/lib/normalize';
import { JobStatusBadge } from '@/components/ui/Badge';
import { TableRowSkeleton } from '@/components/ui/Skeleton';
import { ErrorState, EmptyState } from '@/components/ui/ErrorState';
import { useWSStore } from '@/store/websocket';
import { useToast } from '@/store/toasts';
import { useDebounce } from '@/hooks/useDebounce';
import type { Job, JobStatus } from '@/types';

const FILTER_OPTIONS: { value: JobStatus | 'active' | ''; label: string; count?: number }[] = [
  { value: '',         label: 'Tümü'           },
  { value: 'active',  label: 'Aktif'           },
  { value: 'queued',  label: 'Bekliyor'        },
  { value: 'processing', label: 'İşleniyor'   },
  { value: 'paused',  label: 'Duraklatıldı'   },
  { value: 'completed', label: 'Tamamlandı'   },
  { value: 'retry_wait', label: 'Yeniden Deneme' },
  { value: 'cancelled', label: 'İptal'        },
];

const ACTIVE_STATUSES: JobStatus[] = ['assigned', 'downloading', 'processing', 'uploading', 'paused'];

// ── Live progress bar ─────────────────────────────────────────────────────────

function JobProgressBar({ job }: { job: Job }) {
  const live = useWSStore(s => job.id ? s.jobProgress[job.id] : undefined);
  const pct = asNumber(live?.progress_percent) ?? asNumber(job.progress_percent) ?? 0;
  const eta = fmtETA(job.audio_duration_seconds, pct, 0.38);

  return (
    <div className="flex items-center gap-2 min-w-0 w-full">
      <div
        className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden min-w-0"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`İlerleme: ${pct.toFixed(1)}%`}
      >
        <div
          className={clsx(
            'h-full rounded-full transition-[width] duration-700',
            job.status === 'paused'
              ? 'bg-amber-500'
              : 'bg-gradient-to-r from-indigo-500 to-violet-400',
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-zinc-500 tabular-nums flex-shrink-0 w-28 text-right">
        {pct.toFixed(1)}%{eta ? ` · ${eta}` : ''}
      </span>
    </div>
  );
}

// ── Confirm-on-click cancel button ────────────────────────────────────────────
// BUG-FIX: original fired cancel immediately on click with no confirmation.
// This prevents accidental cancellation of long-running jobs.

function CancelButton({ onConfirm, disabled }: { onConfirm: () => void; disabled?: boolean }) {
  const [confirming, setConfirming] = useState(false);

  if (confirming) {
    return (
      <div className="flex items-center gap-1" role="group" aria-label="İptal onayı">
        <button
          onClick={() => { onConfirm(); setConfirming(false); }}
          className="btn btn-danger text-xs px-2 py-1"
          aria-label="İptal etmeyi onayla"
          autoFocus
        >
          Evet
        </button>
        <button
          onClick={() => setConfirming(false)}
          className="btn btn-ghost text-xs px-2 py-1"
          aria-label="İptal etmeyi reddet"
        >
          Hayır
        </button>
      </div>
    );
  }

  return (
    <button
      onClick={() => setConfirming(true)}
      disabled={disabled}
      className="btn btn-danger p-1.5 text-xs"
      aria-label="İşi iptal et"
      title="İptal Et"
    >
      ✕
    </button>
  );
}

// ── Job row ───────────────────────────────────────────────────────────────────

function JobRow({
  job,
  onAction,
  isPending,
  pendingAction,
}: {
  job: Job;
  onAction: (id: string, action: string) => void;
  isPending: boolean;
  pendingAction: string | null;
}) {
  const canPause  = job.status === 'processing';
  const canResume = job.status === 'paused';
  const canCancel = ['queued', 'assigned', 'downloading', 'processing', 'paused'].includes(job.status);
  const isActive  = ACTIVE_STATUSES.includes(job.status);

  return (
    <div
      className={clsx(
        'table-row px-4 py-3 grid gap-3 items-center',
        'grid-cols-[8rem_1fr_6rem_1fr_3rem_5rem]',
        isPending && 'opacity-60',
      )}
      role="row"
    >
      {/* Status */}
      <div role="cell">
        <JobStatusBadge status={job.status} />
      </div>

      {/* File */}
      <div role="cell" className="min-w-0">
        <p className="text-sm text-zinc-200 truncate font-mono text-xs" title={job.input_path}>
          {job.original_filename}
        </p>
        {job.relative_folder && (
          <p className="text-xs text-zinc-600 truncate">{job.relative_folder}</p>
        )}
      </div>

      {/* Worker */}
      <div role="cell">
        <span className="text-xs text-zinc-500 truncate block">{job.worker_hostname ?? '—'}</span>
      </div>

      {/* Progress / timing */}
      <div role="cell">
        {isActive ? (
          <JobProgressBar job={job} />
        ) : job.processing_time_seconds != null ? (
          <span className="text-xs text-zinc-500 font-mono">
            {fmtDuration(job.processing_time_seconds)}
          </span>
        ) : (
          <span className="text-xs text-zinc-600">{fmtDateTime(job.created_at)}</span>
        )}
      </div>

      {/* RTF */}
      <div role="cell" className="text-right">
        {job.rtf != null && (
          <span className="text-xs font-mono text-zinc-500 tabular-nums">
            {fmtRTF(job.rtf)}
          </span>
        )}
      </div>

      {/* Actions — show loading spinner on the active action */}
      <div role="cell" className="flex items-center justify-end gap-1">
        {isPending ? (
          <span
            className="w-4 h-4 border-2 border-zinc-600 border-t-indigo-400 rounded-full animate-spin"
            aria-label="İşlem devam ediyor"
          />
        ) : (
          <>
            {canPause && (
              <button
                onClick={() => onAction(job.id, 'pause')}
                className="btn btn-ghost p-1.5 text-xs"
                aria-label={`${job.original_filename} işini duraklat`}
                title="Duraklat"
              >
                ⏸
              </button>
            )}
            {canResume && (
              <button
                onClick={() => onAction(job.id, 'resume')}
                className="btn btn-warning p-1.5 text-xs"
                aria-label={`${job.original_filename} işini devam ettir`}
                title="Devam Et"
              >
                ▶
              </button>
            )}
            {canCancel && (
              <CancelButton onConfirm={() => onAction(job.id, 'cancel')} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function Queue() {
  const qc = useQueryClient();
  const toast = useToast();
  const [filterStatus, setFilterStatus] = useState<string>('active');
  const [searchRaw, setSearchRaw] = useState('');
  const [page, setPage] = useState(1);

  // BUG-FIX: debounce the search so every keystroke doesn't fire an API call
  const search = useDebounce(searchRaw, 300);

  const statusParams: JobStatus[] | undefined =
    filterStatus === 'active' ? ACTIVE_STATUSES :
    filterStatus             ? [filterStatus as JobStatus] :
    undefined;

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['jobs', filterStatus, search, page],
    queryFn: () => api.jobs.list({
      status: statusParams,
      filename: search || undefined,
      sort: 'created_at_desc',
      page,
      page_size: 25,
    }),
    refetchInterval: 10_000,
    placeholderData: prev => prev, // Keep previous data while loading new page
  });

  // Track which job is pending which action
  const [pendingJobs, setPendingJobs] = useState<Record<string, string>>({});

  const actionMut = useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) => {
      setPendingJobs(p => ({ ...p, [id]: action }));
      if (action === 'pause')  return api.jobs.pause(id);
      if (action === 'resume') return api.jobs.resume(id);
      if (action === 'cancel') return api.jobs.cancel(id);
      return Promise.resolve();
    },
    onSuccess: (_, { id, action }) => {
      setPendingJobs(p => { const n = { ...p }; delete n[id]; return n; });
      qc.invalidateQueries({ queryKey: ['jobs'] });
      const label = action === 'pause' ? 'duraklatıldı' : action === 'resume' ? 'devam ettirildi' : 'iptal edildi';
      toast.success(`İş ${label}`);
    },
    onError: (_, { id, action }) => {
      setPendingJobs(p => { const n = { ...p }; delete n[id]; return n; });
      toast.error(`İşlem başarısız oldu`);
    },
  });

  const handleAction = (id: string, action: string) => actionMut.mutate({ id, action });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">İş Kuyruğu</h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            {data?.total != null ? `${data.total.toLocaleString('tr')} iş` : 'Yükleniyor…'}
          </p>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Search */}
        <div className="relative">
          <input
            type="search"
            placeholder="Dosya adı ara…"
            value={searchRaw}
            onChange={e => { setSearchRaw(e.target.value); setPage(1); }}
            aria-label="Dosya adına göre ara"
            className="input pr-8 w-52"
          />
          {searchRaw && (
            <button
              onClick={() => { setSearchRaw(''); setPage(1); }}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-600 hover:text-zinc-400 text-sm"
              aria-label="Aramayı temizle"
            >
              ✕
            </button>
          )}
        </div>

        {/* Status filter pills */}
        <div
          className="flex flex-wrap gap-1"
          role="group"
          aria-label="Duruma göre filtrele"
        >
          {FILTER_OPTIONS.map(opt => (
            <button
              key={opt.value}
              onClick={() => { setFilterStatus(opt.value); setPage(1); }}
              aria-pressed={filterStatus === opt.value}
              className={clsx(
                'px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-150',
                filterStatus === opt.value
                  ? 'bg-indigo-950 text-indigo-300 ring-1 ring-indigo-800'
                  : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800',
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Error */}
      {isError && (
        <div className="card">
          <ErrorState onRetry={() => refetch()} compact />
        </div>
      )}

      {/* Table */}
      {!isError && (
        <div
          className="card overflow-hidden"
          role="table"
          aria-label="İş listesi"
          aria-busy={isLoading}
        >
          {/* Column headers */}
          <div
            className="px-4 py-2.5 grid gap-3 bg-zinc-900/80 border-b border-zinc-800 text-xs font-medium text-zinc-500 uppercase tracking-wider"
            style={{ gridTemplateColumns: '8rem 1fr 6rem 1fr 3rem 5rem' }}
            role="row"
          >
            <span role="columnheader">Durum</span>
            <span role="columnheader">Dosya</span>
            <span role="columnheader">İşçi</span>
            <span role="columnheader">İlerleme</span>
            <span role="columnheader" className="text-right">RTF</span>
            <span role="columnheader" className="text-right">İşlemler</span>
          </div>

          {/* Skeleton rows while loading */}
          {isLoading && (
            <div aria-hidden="true">
              {[0,1,2,3,4].map(i => <TableRowSkeleton key={i} />)}
            </div>
          )}

          {/* Empty state */}
          {!isLoading && data?.items.length === 0 && (
            <EmptyState
              icon={filterStatus === 'active' ? '✓' : '◎'}
              title={filterStatus === 'active' ? 'Aktif iş yok' : 'İş bulunamadı'}
              description={
                filterStatus === 'active'
                  ? 'Şu an tüm işçiler boşta.'
                  : search ? `"${search}" için sonuç bulunamadı` : undefined
              }
            />
          )}

          {/* Rows */}
          {!isLoading && data?.items.map(job => (
            <JobRow
              key={job.id}
              job={job}
              onAction={handleAction}
              isPending={job.id in pendingJobs}
              pendingAction={pendingJobs[job.id] ?? null}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {data && data.pages > 1 && (
        <nav
          className="flex items-center justify-between"
          aria-label="Sayfalama"
        >
          <p className="text-sm text-zinc-500">
            {((page - 1) * 25) + 1}–{Math.min(page * 25, data.total)} / {data.total.toLocaleString('tr')} iş
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page === 1}
              aria-label="Önceki sayfa"
              className="btn btn-ghost text-sm"
            >
              ← Önceki
            </button>
            <span className="flex items-center px-3 text-sm text-zinc-500">
              {page} / {data.pages}
            </span>
            <button
              onClick={() => setPage(p => Math.min(data.pages, p + 1))}
              disabled={page === data.pages}
              aria-label="Sonraki sayfa"
              className="btn btn-ghost text-sm"
            >
              Sonraki →
            </button>
          </div>
        </nav>
      )}
    </div>
  );
}
