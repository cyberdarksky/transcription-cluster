import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { WorkerCard } from '@/components/ui/WorkerCard';
import type { Worker } from '@/types';

const STATUS_ORDER: Record<string, number> = {
  busy: 0, idle: 1, paused: 2, online: 3, error: 4, offline: 5,
};

export function Workers() {
  const { data, isLoading } = useQuery({
    queryKey: ['workers'],
    queryFn: api.workers.list,
    refetchInterval: 15_000,
  });

  const workers = [...(data?.items ?? [])].sort(
    (a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9)
  );

  const online = workers.filter(w => w.status !== 'offline' && w.status !== 'error').length;
  const busy = workers.filter(w => w.status === 'busy').length;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">Worker Yönetimi</h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            {online} çevrimiçi · {busy} aktif işleme
          </p>
        </div>
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {[1, 2, 3].map(i => (
            <div key={i} className="card h-64 animate-pulse bg-zinc-900/50" />
          ))}
        </div>
      )}

      {!isLoading && workers.length === 0 && (
        <div className="card px-6 py-16 text-center">
          <p className="text-zinc-500 text-sm">Henüz kayıtlı işçi yok.</p>
          <p className="text-zinc-600 text-xs mt-1">
            Worker kurulum betiğini çalıştırın ve koordinatöre bağlanmasını bekleyin.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {workers.map(w => (
          <WorkerCard key={w.id} worker={w} />
        ))}
      </div>
    </div>
  );
}
