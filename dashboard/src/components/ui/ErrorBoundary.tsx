import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Dashboard render error:', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="card px-6 py-10 text-center max-w-lg mx-auto mt-16">
          <p className="text-rose-400 font-medium mb-2">Arayüz hatası</p>
          <p className="text-sm text-zinc-500 mb-4">
            Sayfa yüklenirken bir hata oluştu. Koordinatör çalışıyorsa sayfayı yenileyin.
          </p>
          <p className="text-xs text-zinc-600 font-mono mb-4 break-all">
            {this.state.error.message}
          </p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => window.location.reload()}
          >
            Sayfayı yenile
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
