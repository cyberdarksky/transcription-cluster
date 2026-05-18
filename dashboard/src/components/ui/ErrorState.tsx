import { clsx } from 'clsx';

interface ErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
  compact?: boolean;
}

export function ErrorState({
  title = 'Bir hata oluştu',
  message = 'Veriler yüklenirken bir sorun oluştu.',
  onRetry,
  compact = false,
}: ErrorStateProps) {
  return (
    <div className={clsx(
      'flex flex-col items-center justify-center text-center',
      compact ? 'py-8 gap-2' : 'py-16 gap-4',
    )}>
      <div className={clsx(
        'rounded-full bg-rose-950/50 text-rose-400 flex items-center justify-center',
        compact ? 'w-8 h-8 text-base' : 'w-12 h-12 text-xl',
      )}>
        ✕
      </div>
      <div>
        <p className={clsx('font-medium text-zinc-300', compact ? 'text-sm' : '')}>{title}</p>
        {!compact && <p className="text-sm text-zinc-600 mt-0.5">{message}</p>}
      </div>
      {onRetry && (
        <button onClick={onRetry} className="btn btn-ghost text-sm">
          Tekrar dene
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  icon = '○',
  title,
  description,
  action,
}: {
  icon?: string;
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
      <div className="w-12 h-12 rounded-full bg-zinc-800 flex items-center justify-center text-2xl">
        {icon}
      </div>
      <div>
        <p className="font-medium text-zinc-400">{title}</p>
        {description && <p className="text-sm text-zinc-600 mt-0.5">{description}</p>}
      </div>
      {action}
    </div>
  );
}
