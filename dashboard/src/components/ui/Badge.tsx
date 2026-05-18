import { clsx } from 'clsx';
import type { JobStatus, WorkerStatus } from '@/types';
import { fmtJobStatus, fmtWorkerStatus } from '@/lib/format';

const JOB_COLORS: Record<JobStatus, string> = {
  queued:      'bg-zinc-800 text-zinc-300',
  assigned:    'bg-blue-950 text-blue-300',
  downloading: 'bg-cyan-950 text-cyan-300',
  processing:  'bg-indigo-950 text-indigo-300',
  uploading:   'bg-violet-950 text-violet-300',
  completed:   'bg-emerald-950 text-emerald-300',
  failed:      'bg-rose-950 text-rose-300',
  paused:      'bg-amber-950 text-amber-300',
  retry_wait:  'bg-orange-950 text-orange-300',
  cancelled:   'bg-zinc-800 text-zinc-500',
};

const WORKER_COLORS: Record<WorkerStatus, string> = {
  online:  'bg-emerald-950 text-emerald-300',
  idle:    'bg-zinc-800 text-zinc-300',
  busy:    'bg-indigo-950 text-indigo-300',
  paused:  'bg-amber-950 text-amber-300',
  offline: 'bg-zinc-900 text-zinc-600',
  error:   'bg-rose-950 text-rose-300',
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <span className={clsx('badge', JOB_COLORS[status])}>
      {status === 'processing' && (
        <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" />
      )}
      {fmtJobStatus(status)}
    </span>
  );
}

export function WorkerStatusBadge({ status }: { status: WorkerStatus }) {
  return (
    <span className={clsx('badge', WORKER_COLORS[status])}>
      {(status === 'busy' || status === 'idle') && (
        <span className={clsx(
          'w-1.5 h-1.5 rounded-full',
          status === 'busy' ? 'bg-indigo-400 animate-pulse' : 'bg-zinc-500',
        )} />
      )}
      {fmtWorkerStatus(status)}
    </span>
  );
}
