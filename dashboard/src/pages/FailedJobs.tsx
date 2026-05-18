import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { api } from '@/lib/api';
import { fmtDateTime, fmtDuration } from '@/lib/format';

export function FailedJobs() {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['jobs', 'failed'],
    queryFn: () => api.jobs.list({ status: ['failed', 'cancelled'], sort: 'created_at_desc', page_size: 50 }),
    refetchInterval: 30_000,
  });

  const retryMut = useMutation({
    mutationFn: (id: string) => api.jobs.retry(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['jobs'] }),
  });

  const jobs = data?.items ?? [];
  const failed = jobs.filter(j => j.status === 'failed');
  const cancelled = jobs.filter(j => j.status === 'cancelled');

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">Başarısız İşler</h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            {failed.length} başarısız · {cancelled.length} iptal
          </p>
        </div>
      </div>

      {isLoading && (
        <div className="card py-16 text-center text-zinc-600 text-sm">Yükleniyor…</div>
      )}

      {!isLoading && jobs.length === 0 && (
        <div className="card px-6 py-16 text-center">
          <p className="text-4xl mb-4">✓</p>
          <p className="text-zinc-400 font-medium">Başarısız iş yok!</p>
          <p className="text-zinc-600 text-sm mt-1">Tüm işler başarıyla tamamlandı.</p>
        </div>
      )}

      <div className="card overflow-hidden">
        {jobs.map(job => (
          <div key={job.id} className="border-b border-zinc-800/60 last:border-0">
            {/* Row */}
            <div
              className="px-4 py-3 flex items-center gap-4 hover:bg-zinc-800/30 cursor-pointer transition-colors"
              onClick={() => setExpanded(expanded === job.id ? null : job.id)}
            >
              {/* Category indicator */}
              <div className={clsx(
                'w-2 h-2 rounded-full flex-shrink-0',
                job.status === 'failed'
                  ? job.error_category === 'deterministic' ? 'bg-rose-500' : 'bg-amber-500'
                  : 'bg-zinc-600',
              )} />

              {/* File info */}
              <div className="flex-1 min-w-0">
                <p className="text-sm text-zinc-200 truncate font-mono text-xs">{job.original_filename}</p>
                <p className="text-xs text-zinc-600 truncate">{job.relative_folder || job.input_path}</p>
              </div>

              {/* Meta */}
              <div className="hidden md:flex items-center gap-6 text-xs text-zinc-500">
                <span>{fmtDateTime(job.created_at)}</span>
                <span>{job.retry_count}/{job.max_retries} deneme</span>
                <span className={clsx(
                  'badge',
                  job.error_category === 'deterministic' ? 'bg-rose-950 text-rose-400' : 'bg-amber-950 text-amber-400',
                )}>
                  {job.error_category === 'deterministic' ? 'Kalıcı hata' : 'Geçici hata'}
                </span>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-2">
                {job.status === 'failed' && (
                  <button
                    onClick={e => { e.stopPropagation(); retryMut.mutate(job.id); }}
                    disabled={retryMut.isPending && retryMut.variables === job.id}
                    className="btn btn-ghost text-xs py-1"
                  >
                    {retryMut.variables === job.id && retryMut.isPending ? '…' : 'Yeniden Dene'}
                  </button>
                )}
                <span className={clsx('text-zinc-600 text-xs', expanded === job.id ? 'rotate-180' : '')}>▾</span>
              </div>
            </div>

            {/* Expanded error */}
            {expanded === job.id && (
              <div className="px-4 pb-4 bg-zinc-950/40 animate-enter">
                <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-3">
                  <p className="text-xs text-zinc-500 font-medium mb-1">Son Hata Mesajı</p>
                  <pre className="text-xs text-rose-300 font-mono whitespace-pre-wrap leading-relaxed max-h-40 overflow-y-auto">
                    {job.last_error ?? 'Hata detayı yok'}
                  </pre>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
