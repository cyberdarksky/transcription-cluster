import { clsx } from 'clsx';

interface SkeletonProps {
  className?: string;
  width?: string;
  height?: string;
}

export function Skeleton({ className, width, height }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={clsx('skeleton', className)}
      style={{ width, height }}
    />
  );
}

// ── Pre-built skeleton shapes ─────────────────────────────────────────────────

export function StatCardSkeleton() {
  return (
    <div className="card px-5 py-4 space-y-3">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-8 w-16" />
      <Skeleton className="h-2.5 w-32" />
    </div>
  );
}

export function WorkerCardSkeleton() {
  return (
    <div className="card flex flex-col" aria-hidden="true">
      <div className="px-5 py-4 flex items-start justify-between gap-3">
        <div className="space-y-2 flex-1">
          <Skeleton className="h-4 w-36" />
          <Skeleton className="h-3 w-48" />
          <Skeleton className="h-3 w-24" />
        </div>
        <Skeleton className="h-6 w-16 rounded-md" />
      </div>
      <div className="px-5 pb-4 space-y-3">
        {['CPU', 'Bellek', 'GPU'].map(label => (
          <div key={label} className="space-y-1.5">
            <div className="flex justify-between">
              <Skeleton className="h-3 w-8" />
              <Skeleton className="h-3 w-8" />
            </div>
            <Skeleton className="h-1.5 w-full rounded-full" />
          </div>
        ))}
      </div>
      <div className="px-5 py-3 border-t border-zinc-800 flex justify-between">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-3 w-20" />
      </div>
    </div>
  );
}

export function TableRowSkeleton() {
  return (
    <div className="px-4 py-3 grid grid-cols-12 gap-3 items-center border-b border-zinc-800/60" aria-hidden="true">
      <div className="col-span-2"><Skeleton className="h-5 w-20 rounded-md" /></div>
      <div className="col-span-4 space-y-1.5">
        <Skeleton className="h-3 w-36" />
        <Skeleton className="h-2.5 w-24" />
      </div>
      <div className="col-span-2"><Skeleton className="h-3 w-20" /></div>
      <div className="col-span-2"><Skeleton className="h-1.5 w-full rounded-full" /></div>
      <div className="col-span-1 flex justify-end"><Skeleton className="h-3 w-12" /></div>
      <div className="col-span-1" />
    </div>
  );
}

export function OverviewSkeleton() {
  return (
    <div className="space-y-6" aria-label="Yükleniyor…">
      <div className="space-y-1">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-4 w-56" />
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[0, 1, 2, 3].map(i => <StatCardSkeleton key={i} />)}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card lg:col-span-2">
          <div className="card-header"><Skeleton className="h-4 w-40" /></div>
          <div className="p-4"><Skeleton className="h-44 w-full rounded-lg" /></div>
        </div>
        <div className="card">
          <div className="card-header"><Skeleton className="h-4 w-24" /></div>
          <div className="p-4 space-y-4">
            {[0, 1, 2, 3].map(i => (
              <div key={i} className="space-y-1.5">
                <div className="flex justify-between">
                  <Skeleton className="h-3 w-20" />
                  <Skeleton className="h-3 w-8" />
                </div>
                <Skeleton className="h-1.5 w-full rounded-full" />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
