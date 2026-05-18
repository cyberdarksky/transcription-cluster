import { clsx } from 'clsx';

interface MetricBarProps {
  label: string;
  value: number | null | undefined;
  max?: number;
  color?: 'indigo' | 'emerald' | 'amber' | 'rose' | 'violet';
  className?: string;
}

const COLOR_MAP = {
  indigo: 'from-indigo-500 to-indigo-400',
  emerald: 'from-emerald-500 to-emerald-400',
  amber: 'from-amber-500 to-amber-400',
  rose: 'from-rose-500 to-rose-400',
  violet: 'from-violet-500 to-violet-400',
};

function pickColor(value: number): 'emerald' | 'amber' | 'rose' {
  if (value < 60) return 'emerald';
  if (value < 80) return 'amber';
  return 'rose';
}

export function MetricBar({
  label, value, max = 100, color, className,
}: MetricBarProps) {
  const pct = value != null ? Math.min(100, (value / max) * 100) : 0;
  const effectiveColor = color ?? (value != null ? pickColor(pct) : 'indigo');
  const gradient = COLOR_MAP[effectiveColor];

  return (
    <div className={clsx('space-y-1', className)}>
      <div className="flex justify-between items-center">
        <span className="text-xs text-zinc-500 font-medium">{label}</span>
        <span className="text-xs font-mono text-zinc-300">
          {value != null ? `${Math.round(value)}%` : '—'}
        </span>
      </div>
      <div className="gauge-bar">
        <div
          className={clsx('gauge-fill bg-gradient-to-r', gradient)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
