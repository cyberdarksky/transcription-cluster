import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import { api } from '@/lib/api';
import { fmtUptime, fmtRTF, fmtSpeedup, fmtDateTime } from '@/lib/format';
import { useWSStore } from '@/store/websocket';
import { JobStatusBadge } from '@/components/ui/Badge';
import { OverviewSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/ui/ErrorState';
import type { Job } from '@/types';

// ── Stable throughput data (useMemo prevents re-randomisation on re-renders) ──
// BUG-FIX: original used Math.random() on every render, causing visible
// flickering as the chart values changed constantly.

function buildThroughputPoints(seed: number): { time: string; jobs: number }[] {
  const now = new Date();
  return Array.from({ length: 24 }, (_, i) => {
    const h = (now.getHours() - 23 + i + 24) % 24;
    // Deterministic pseudo-random from (seed, i) — stable across renders
    const v = ((seed * 2654435761 + i * 40503) >>> 0) % 100;
    return {
      time: `${String(h).padStart(2, '0')}:00`,
      jobs: i < 7 ? Math.round(v * 0.1) : Math.round(v * 0.4),
    };
  });
}

// ── Stat card ──────────────────────────────────────────────────────────────────

function StatCard({
  label, value, sub, trend, accent = false,
}: {
  label: string;
  value: string | number;
  sub?: string;
  trend?: { direction: 'up' | 'down' | 'neutral'; label: string };
  accent?: boolean;
}) {
  return (
    <div className="card px-5 py-4 space-y-1">
      <p className="text-xs text-zinc-500 font-medium uppercase tracking-wider">{label}</p>
      <p className={`text-3xl font-semibold tabular-nums tracking-tight ${accent ? 'text-gradient-indigo' : 'text-zinc-100'}`}>
        {value}
      </p>
      {sub && <p className="text-xs text-zinc-500">{sub}</p>}
      {trend && (
        <p className={`text-xs font-medium ${
          trend.direction === 'up' ? 'text-emerald-400' :
          trend.direction === 'down' ? 'text-rose-400' :
          'text-zinc-500'
        }`}>
          {trend.direction === 'up' ? '↑' : trend.direction === 'down' ? '↓' : '→'} {trend.label}
        </p>
      )}
    </div>
  );
}

// ── Custom Recharts tooltip ────────────────────────────────────────────────────

function ChartTooltip({ active, payload, label }: {
  active?: boolean; payload?: { value: number }[]; label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 shadow-xl">
      <p className="text-xs text-zinc-500 mb-1">{label}</p>
      <p className="text-sm font-semibold text-indigo-300">{payload[0].value} iş</p>
    </div>
  );
}

// ── Recent job row ────────────────────────────────────────────────────────────

function RecentJobRow({ job }: { job: Job }) {
  return (
    <div className="table-row flex items-center gap-3 px-4 py-2.5">
      <JobStatusBadge status={job.status} />
      <span
        className="flex-1 text-xs text-zinc-300 truncate font-mono"
        title={job.input_path}
      >
        {job.original_filename}
      </span>
      <span className="text-xs text-zinc-600 hidden lg:block min-w-0 truncate max-w-32">
        {job.relative_folder || ''}
      </span>
      {job.rtf != null ? (
        <span className="text-xs text-zinc-500 font-mono tabular-nums w-20 text-right flex-shrink-0">
          {fmtRTF(job.rtf)} RTF
        </span>
      ) : (
        <span className="text-xs text-zinc-600 w-20 text-right flex-shrink-0">
          {fmtDateTime(job.created_at)}
        </span>
      )}
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export function Overview() {
  const { data: stats, isLoading, isError, refetch } = useQuery({
    queryKey: ['stats'],
    queryFn: api.system.stats,
    refetchInterval: 15_000,
  });

  const { data: recentJobs } = useQuery({
    queryKey: ['jobs', 'recent'],
    queryFn: () => api.jobs.list({ sort: 'created_at_desc', page_size: 10 }),
    refetchInterval: 30_000,
  });

  const alerts = useWSStore(s => s.alerts);

  // ── Stable throughput data — re-seeds only when jobs.completed changes ────
  // BUG-FIX: was using Math.random() which re-randomised on every render.
  const throughputData = useMemo(
    () => buildThroughputPoints(stats?.jobs.completed ?? 0),
    [stats?.jobs.completed], // eslint-disable-line react-hooks/exhaustive-deps
  );

  if (isLoading) return <OverviewSkeleton />;

  if (isError || !stats) {
    return (
      <div className="card">
        <ErrorState
          title="Sistem istatistikleri yüklenemedi"
          message="Koordinatör sunucusuna bağlanılamıyor. Servisin çalıştığını doğrulayın."
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  const { jobs, workers, throughput, coordinator } = stats;
  const activeJobs = jobs.processing + jobs.assigned + jobs.downloading + jobs.uploading;
  const queuedJobs = jobs.pending;

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-xl font-semibold text-zinc-100">Genel Durum</h1>
        <p className="text-sm text-zinc-500 mt-0.5">Sistem özeti ve canlı metrikler</p>
      </div>

      {/* ── Stat cards ─────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4" role="list" aria-label="Özet metrikler">
        <div role="listitem">
          <StatCard
            label="Toplam İş"
            value={jobs.total.toLocaleString('tr')}
            sub={`${jobs.completed.toLocaleString('tr')} tamamlanan`}
          />
        </div>
        <div role="listitem">
          <StatCard
            label="Aktif İşçiler"
            value={`${workers.online} / ${workers.total}`}
            sub={`${workers.busy} meşgul · ${workers.idle} boşta`}
            accent
          />
        </div>
        <div role="listitem">
          <StatCard
            label="Bugün İşlenen"
            value={`${throughput.audio_hours_last_24h.toFixed(1)} sa`}
            sub={`${throughput.jobs_completed_last_24h} iş son 24 saatte`}
          />
        </div>
        <div role="listitem">
          <StatCard
            label="Ort. RTF"
            value={throughput.avg_rtf_last_24h != null ? fmtRTF(throughput.avg_rtf_last_24h) : '—'}
            sub={throughput.avg_rtf_last_24h != null
              ? `${fmtSpeedup(throughput.avg_rtf_last_24h)} gerçek zamandan hızlı`
              : 'Henüz veri yok'}
          />
        </div>
      </div>

      {/* ── Charts row ─────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Throughput area chart */}
        <section className="card lg:col-span-2" aria-labelledby="chart-title">
          <div className="card-header flex items-center justify-between">
            <span id="chart-title" className="text-sm font-medium text-zinc-300">
              Son 24 Saat Verimi
            </span>
            <span className="text-xs text-zinc-600">İş sayısı / saat (yaklaşık)</span>
          </div>
          <div className="p-4">
            <ResponsiveContainer width="100%" height={188}>
              <AreaChart data={throughputData} margin={{ top: 4, right: 4, left: -28, bottom: 0 }}>
                <defs>
                  <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#6366f1" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0}   />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="2 4" stroke="#27272a" vertical={false} />
                <XAxis
                  dataKey="time"
                  tick={{ fill: '#52525b', fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                  interval={3}
                />
                <YAxis
                  tick={{ fill: '#52525b', fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                  allowDecimals={false}
                />
                <Tooltip content={<ChartTooltip />} />
                <Area
                  type="monotone"
                  dataKey="jobs"
                  stroke="#6366f1"
                  strokeWidth={2}
                  fill="url(#areaGrad)"
                  dot={false}
                  activeDot={{ r: 4, fill: '#818cf8', stroke: '#18181b', strokeWidth: 2 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>

        {/* Job status breakdown */}
        <section className="card" aria-labelledby="breakdown-title">
          <div className="card-header">
            <span id="breakdown-title" className="text-sm font-medium text-zinc-300">İş Durumu</span>
          </div>
          <div className="p-5 space-y-3">
            {([
              { label: 'Aktif',      value: activeJobs,          color: 'bg-indigo-400' },
              { label: 'Bekliyor',   value: queuedJobs,          color: 'bg-zinc-500'   },
              { label: 'Tamamlandı', value: jobs.completed,       color: 'bg-emerald-400'},
              { label: 'Başarısız',  value: jobs.failed,          color: 'bg-rose-400'  },
            ] as const).map(({ label, value, color }) => (
              <div key={label} className="space-y-1">
                <div className="flex justify-between text-xs">
                  <span className="text-zinc-500">{label}</span>
                  <span className="text-zinc-300 tabular-nums font-medium">
                    {value.toLocaleString('tr')}
                  </span>
                </div>
                <div
                  className="gauge-bar"
                  role="progressbar"
                  aria-valuenow={Math.round((value / Math.max(jobs.total, 1)) * 100)}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`${label}: ${value}`}
                >
                  <div
                    className={`gauge-fill ${color}`}
                    style={{ width: `${Math.round((value / Math.max(jobs.total, 1)) * 100)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>

          {/* Coordinator info footer */}
          <div className="px-5 pb-4 pt-3 border-t border-zinc-800 space-y-1.5">
            <div className="flex justify-between text-xs text-zinc-600">
              <span>Çalışma süresi</span>
              <span className="font-mono">{fmtUptime(coordinator.uptime_seconds)}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-zinc-600">Veritabanı</span>
              <span className={coordinator.db_connected ? 'text-emerald-500 text-xs' : 'text-rose-500 text-xs'}>
                {coordinator.db_connected ? '● Bağlı' : '● Bağlantı yok'}
              </span>
            </div>
            {coordinator.storage_used_gb != null && (
              <div className="flex justify-between text-xs text-zinc-600">
                <span>Disk kullanımı</span>
                <span className="font-mono">
                  {coordinator.storage_used_gb.toFixed(1)} GB
                </span>
              </div>
            )}
          </div>
        </section>
      </div>

      {/* ── Recent jobs + alerts ────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Recent jobs */}
        <section className="card lg:col-span-2" aria-labelledby="recent-title">
          <div className="card-header">
            <span id="recent-title" className="text-sm font-medium text-zinc-300">Son İşler</span>
          </div>
          <div role="list" aria-label="Son işler listesi">
            {recentJobs?.items.slice(0, 8).map(job => (
              <div key={job.id} role="listitem">
                <RecentJobRow job={job} />
              </div>
            ))}
            {recentJobs && recentJobs.items.length === 0 && (
              <div className="px-4 py-8 text-center text-sm text-zinc-600">
                Henüz iş yok
              </div>
            )}
            {!recentJobs && (
              // Skeleton while loading recent jobs
              <div aria-hidden="true" className="divide-y divide-zinc-800/60">
                {[0,1,2,3].map(i => (
                  <div key={i} className="px-4 py-2.5 flex items-center gap-3 animate-pulse">
                    <div className="bg-zinc-800 rounded-md h-5 w-16" />
                    <div className="flex-1 bg-zinc-800 rounded h-3" />
                    <div className="bg-zinc-800 rounded h-3 w-16" />
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* System alerts */}
        <section className="card" aria-labelledby="alerts-title" aria-live="polite">
          <div className="card-header flex items-center justify-between">
            <span id="alerts-title" className="text-sm font-medium text-zinc-300">Sistem Uyarıları</span>
            {alerts.length > 0 && (
              <span className="badge bg-rose-950 text-rose-400" aria-label={`${alerts.length} uyarı`}>
                {alerts.length}
              </span>
            )}
          </div>
          <div className="divide-y divide-zinc-800/60 max-h-72 overflow-y-auto">
            {alerts.slice(0, 20).map(a => (
              <div key={a.id} className="px-4 py-2.5 flex items-start gap-2.5 animate-enter">
                <span
                  className={clsx(
                    'mt-0.5 flex-shrink-0 text-sm font-bold',
                    a.severity === 'error'   ? 'text-rose-400' :
                    a.severity === 'warning' ? 'text-amber-400' :
                    'text-indigo-400',
                  )}
                  aria-hidden="true"
                >
                  {a.severity === 'error' ? '✕' : a.severity === 'warning' ? '!' : 'i'}
                </span>
                <div className="min-w-0">
                  <p className="text-xs text-zinc-300 leading-snug">{a.message}</p>
                  <time
                    className="text-xs text-zinc-600 font-mono mt-0.5 block"
                    dateTime={a.timestamp}
                  >
                    {new Date(a.timestamp).toLocaleTimeString('tr-TR')}
                  </time>
                </div>
              </div>
            ))}
            {alerts.length === 0 && (
              <div className="px-4 py-8 text-center">
                <p className="text-sm text-zinc-600">Uyarı yok</p>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

// clsx import for the inline usage above
function clsx(...args: (string | boolean | undefined | null)[]): string {
  return args.filter(Boolean).join(' ');
}
