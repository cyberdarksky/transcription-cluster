import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ErrorBoundary } from '@/components/ui/ErrorBoundary';
import { Layout } from '@/components/layout/Layout';
import { Overview } from '@/pages/Overview';
import { Workers } from '@/pages/Workers';
import { Queue } from '@/pages/Queue';
import { FailedJobs } from '@/pages/FailedJobs';
import { LogViewer } from '@/pages/LogViewer';
import { Settings } from '@/pages/Settings';

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={qc}>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="workers" element={<Workers />} />
            <Route path="queue" element={<Queue />} />
            <Route path="failed" element={<FailedJobs />} />
            <Route path="logs" element={<LogViewer />} />
            <Route path="settings" element={<Settings />} />
          </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
