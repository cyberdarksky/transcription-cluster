import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { Toaster } from '@/components/ui/Toast';
import { useDashboardWebSocket } from '@/hooks/useWebSocket';

export function Layout() {
  useDashboardWebSocket();

  return (
    <div className="flex h-screen overflow-hidden bg-zinc-950">
      <Sidebar />
      <main id="main-content" className="flex-1 overflow-y-auto">
        <div className="max-w-7xl mx-auto px-6 py-8">
          <Outlet />
        </div>
      </main>
      <Toaster />
    </div>
  );
}
