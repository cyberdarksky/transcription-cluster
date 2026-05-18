import { clsx } from 'clsx';
import { useToastStore } from '@/store/toasts';
import type { ToastItem } from '@/store/toasts';

const TYPE_STYLES = {
  success: {
    bar:   'bg-emerald-500',
    icon:  '✓',
    text:  'text-emerald-400',
    bg:    'bg-zinc-900 border-zinc-700',
  },
  error: {
    bar:   'bg-rose-500',
    icon:  '✕',
    text:  'text-rose-400',
    bg:    'bg-zinc-900 border-zinc-700',
  },
  warning: {
    bar:   'bg-amber-500',
    icon:  '⚠',
    text:  'text-amber-400',
    bg:    'bg-zinc-900 border-zinc-700',
  },
  info: {
    bar:   'bg-indigo-500',
    icon:  'ℹ',
    text:  'text-indigo-400',
    bg:    'bg-zinc-900 border-zinc-700',
  },
} as const;

function ToastEntry({ toast }: { toast: ToastItem }) {
  const dismiss = useToastStore(s => s.dismiss);
  const styles = TYPE_STYLES[toast.type];

  return (
    <div
      role="alert"
      aria-live="polite"
      className={clsx(
        'relative flex items-center gap-3 px-4 py-3 rounded-xl border shadow-xl',
        'min-w-72 max-w-sm pointer-events-auto',
        styles.bg,
        toast.dismissing ? 'animate-toast-out' : 'animate-toast-in',
      )}
    >
      {/* Accent bar */}
      <div className={clsx('absolute left-0 top-0 bottom-0 w-1 rounded-l-xl', styles.bar)} />

      {/* Icon */}
      <span className={clsx('text-sm font-bold flex-shrink-0 ml-1', styles.text)}>
        {styles.icon}
      </span>

      {/* Message */}
      <p className="flex-1 text-sm text-zinc-200 leading-snug">{toast.message}</p>

      {/* Dismiss */}
      <button
        onClick={() => dismiss(toast.id)}
        aria-label="Bildirimi kapat"
        className="btn-ghost p-1 rounded-md text-zinc-600 hover:text-zinc-400 flex-shrink-0"
      >
        ✕
      </button>
    </div>
  );
}

export function Toaster() {
  const toasts = useToastStore(s => s.toasts);

  if (toasts.length === 0) return null;

  return (
    <div
      aria-label="Bildirimler"
      className="fixed bottom-5 right-5 z-50 flex flex-col gap-2.5 pointer-events-none"
    >
      {toasts.map(t => (
        <ToastEntry key={t.id} toast={t} />
      ))}
    </div>
  );
}
