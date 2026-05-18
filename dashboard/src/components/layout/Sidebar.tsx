import { NavLink } from 'react-router-dom';
import { clsx } from 'clsx';
import { useWSStore } from '@/store/websocket';

// Using distinct, unambiguous Unicode symbols — no emoji dependency
const NAV = [
  { to: '/',         symbol: '▦', label: 'Genel Durum',        ariaLabel: 'Genel Durum sayfasına git' },
  { to: '/workers',  symbol: '◎', label: 'Worker Yönetimi',    ariaLabel: 'Worker Yönetimi sayfasına git' },
  { to: '/queue',    symbol: '≡', label: 'İş Kuyruğu',         ariaLabel: 'İş Kuyruğu sayfasına git' },
  { to: '/failed',   symbol: '◇', label: 'Başarısız İşler',    ariaLabel: 'Başarısız İşler sayfasına git' },
  { to: '/logs',     symbol: '⋮', label: 'Loglar',             ariaLabel: 'Log Görüntüleyici sayfasına git' },
  { to: '/settings', symbol: '⚙', label: 'Ayarlar',            ariaLabel: 'Ayarlar sayfasına git' },
];

export function Sidebar() {
  const { connected, coordinatorVersion } = useWSStore();

  return (
    <aside
      role="navigation"
      aria-label="Ana menü"
      className="w-60 flex-shrink-0 h-screen sticky top-0 bg-zinc-900 border-r border-zinc-800 flex flex-col"
    >
      {/* Logo */}
      <div className="px-4 py-5 border-b border-zinc-800">
        <div className="flex items-center gap-3">
          <div
            className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center flex-shrink-0"
            aria-hidden="true"
          >
            <span className="text-white text-sm font-bold">T</span>
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-zinc-100 leading-tight">Transkripsiyon</p>
            <p className="text-xs text-zinc-500 leading-tight">Kümesi</p>
          </div>
        </div>
      </div>

      {/* Nav links */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto" aria-label="Sayfalar">
        {NAV.map(({ to, symbol, label, ariaLabel }) => (
          <NavLink key={to} to={to} end={to === '/'} aria-label={ariaLabel}>
            {({ isActive }) => (
              <span className={clsx(
                'nav-item group',
                isActive ? 'nav-item-active' : 'nav-item-default',
              )}>
                <span
                  className="w-5 text-center text-sm leading-none select-none flex-shrink-0"
                  aria-hidden="true"
                >
                  {symbol}
                </span>
                <span>{label}</span>
                {/* Active indicator */}
                {isActive && (
                  <span className="ml-auto w-1 h-4 rounded-full bg-indigo-400 opacity-60" aria-hidden="true" />
                )}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Connection status */}
      <div className="px-4 py-4 border-t border-zinc-800" role="status" aria-live="polite">
        <div className="flex items-center gap-2.5">
          <div
            className={clsx(
              'w-2 h-2 rounded-full flex-shrink-0 transition-colors duration-500',
              connected ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-600',
            )}
            aria-hidden="true"
          />
          <div className="min-w-0">
            <p className={clsx(
              'text-xs font-medium truncate transition-colors duration-300',
              connected ? 'text-emerald-400' : 'text-zinc-500',
            )}>
              {connected ? 'Canlı bağlantı' : 'Yeniden bağlanıyor…'}
            </p>
            {coordinatorVersion && (
              <p className="text-xs text-zinc-700 font-mono">v{coordinatorVersion}</p>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
}
