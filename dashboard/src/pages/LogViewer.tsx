import { useEffect, useRef, useState, useCallback } from 'react';
import { clsx } from 'clsx';
import { useWSStore } from '@/store/websocket';
import { useDebounce } from '@/hooks/useDebounce';
import type { LogEntry } from '@/types';

const LEVEL_STYLES: Record<LogEntry['level'], { text: string; label: string; bg: string }> = {
  info:    { text: 'text-zinc-500',   label: 'INFO', bg: '' },
  success: { text: 'text-emerald-400', label: 'DONE', bg: 'bg-emerald-950/20' },
  warning: { text: 'text-amber-400',  label: 'WARN', bg: 'bg-amber-950/20'  },
  error:   { text: 'text-rose-400',   label: 'FAIL', bg: 'bg-rose-950/20'   },
};

function LogRow({ entry }: { entry: LogEntry }) {
  const s = LEVEL_STYLES[entry.level];
  const t = new Date(entry.timestamp).toLocaleTimeString('tr-TR', {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });

  return (
    <div
      className={clsx(
        'flex items-baseline gap-3 px-4 py-1 hover:bg-zinc-800/20',
        s.bg,
      )}
      role="row"
    >
      <time
        className="text-zinc-700 font-mono text-xs flex-shrink-0 w-20 select-none"
        dateTime={entry.timestamp}
      >
        {t}
      </time>
      <span className={clsx('font-mono text-xs font-bold flex-shrink-0 w-10', s.text)}>
        {s.label}
      </span>
      <span className={clsx('text-xs font-mono leading-relaxed', s.text)}>
        {entry.message}
      </span>
    </div>
  );
}

export function LogViewer() {
  const logs = useWSStore(s => s.logs);
  const clearLogs = useWSStore(s => s.clearLogs);

  const [autoScroll, setAutoScroll] = useState(true);
  const [filterLevel, setFilterLevel] = useState<LogEntry['level'] | ''>('');
  const [searchRaw, setSearchRaw] = useState('');
  const search = useDebounce(searchRaw, 200);

  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Filter: logs in store are newest-first; render oldest-first (terminal style)
  const filtered = logs.filter(l => {
    if (filterLevel && l.level !== filterLevel) return false;
    if (search && !l.message.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });
  // Reverse so oldest entry is at top, newest at bottom
  const displayLogs = [...filtered].reverse();

  // ── Auto-scroll to bottom (newest entry) ──────────────────────────────────
  useEffect(() => {
    if (autoScroll) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs.length, autoScroll]);

  // ── Re-enable auto-scroll when user scrolls back to the bottom ────────────
  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceFromBottom > 80 && autoScroll) {
      setAutoScroll(false);
    } else if (distanceFromBottom <= 40 && !autoScroll) {
      // BUG-FIX: original never re-enabled autoscroll — once you scrolled up,
      // it stayed disabled until the checkbox was manually clicked.
      setAutoScroll(true);
    }
  }, [autoScroll]);

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    setAutoScroll(true);
  };

  const LEVELS = [
    { value: '', label: 'Tümü' },
    { value: 'success', label: 'Başarı' },
    { value: 'info',    label: 'Bilgi'  },
    { value: 'warning', label: 'Uyarı'  },
    { value: 'error',   label: 'Hata'   },
  ] as const;

  return (
    <div className="flex flex-col gap-4" style={{ height: 'calc(100vh - 8rem)' }}>
      {/* Header + controls */}
      <div className="flex items-center justify-between flex-wrap gap-3 flex-shrink-0">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">Log Görüntüleyici</h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            Gerçek zamanlı sistem olayları
            {filtered.length > 0 && (
              <span className="ml-1 text-zinc-600">({filtered.length.toLocaleString('tr')} kayıt)</span>
            )}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Search */}
          <div className="relative">
            <input
              type="search"
              placeholder="Logda ara…"
              value={searchRaw}
              onChange={e => setSearchRaw(e.target.value)}
              aria-label="Loglarda ara"
              className="input text-xs w-40"
            />
          </div>

          {/* Level filter */}
          <div className="flex gap-1" role="group" aria-label="Seviyeye göre filtrele">
            {LEVELS.map(l => (
              <button
                key={l.value}
                onClick={() => setFilterLevel(l.value as LogEntry['level'] | '')}
                aria-pressed={filterLevel === l.value}
                className={clsx(
                  'px-2.5 py-1 rounded text-xs font-medium transition-colors',
                  filterLevel === l.value
                    ? 'bg-indigo-950 text-indigo-300'
                    : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800',
                )}
              >
                {l.label}
              </button>
            ))}
          </div>

          {/* Auto-scroll toggle */}
          <label className="flex items-center gap-1.5 text-xs text-zinc-500 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={e => setAutoScroll(e.target.checked)}
              className="rounded accent-indigo-500"
              aria-label="Otomatik kaydırma"
            />
            Otomatik kaydır
          </label>

          <button
            onClick={clearLogs}
            className="btn btn-ghost text-xs"
            aria-label="Tüm logları temizle"
          >
            Temizle
          </button>
        </div>
      </div>

      {/* Log container */}
      <div className="card overflow-hidden flex-1 flex flex-col min-h-0 relative">
        {/* Scroll-to-bottom FAB — appears when auto-scroll is off */}
        {!autoScroll && (
          <button
            onClick={scrollToBottom}
            aria-label="En alta kaydır (en yeni log)"
            className={clsx(
              'absolute bottom-4 right-4 z-10',
              'flex items-center gap-1.5 px-3 py-1.5 rounded-full',
              'bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium',
              'shadow-lg shadow-indigo-900/50 transition-all duration-150',
              'animate-enter',
            )}
          >
            ↓ En yeni
          </button>
        )}

        {/* Log lines */}
        <div
          ref={containerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto font-mono bg-zinc-950/60"
          role="log"
          aria-live="polite"
          aria-label="Sistem log akışı"
        >
          {displayLogs.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-zinc-600">
              <span className="text-3xl">≡</span>
              <p className="text-sm">
                {logs.length === 0
                  ? 'WebSocket bağlantısı bekleniyor…'
                  : 'Filtre koşuluna uyan log yok'}
              </p>
            </div>
          )}

          {displayLogs.map(entry => (
            <LogRow key={entry.id} entry={entry} />
          ))}

          {/* Auto-scroll target */}
          <div ref={bottomRef} aria-hidden="true" />
        </div>
      </div>
    </div>
  );
}
