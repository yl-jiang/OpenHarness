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
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center min-h-[50vh] px-8 text-center">
          <div className="w-12 h-12 mb-4 grid place-items-center rounded-lg bg-danger/10 text-danger text-xl">!</div>
          <h2 className="font-serif text-xl text-text m-0 mb-2">Something went wrong</h2>
          <p className="text-[13px] text-text-muted max-w-md m-0 mb-4 leading-relaxed">
            {this.state.error.message || 'An unexpected error occurred.'}
          </p>
          <button
            onClick={() => { this.setState({ error: null }); window.location.reload(); }}
            className="text-[12px] px-4 py-2 rounded-md border border-border bg-surface-2 text-text-secondary hover:text-text cursor-pointer transition-colors"
          >
            Reload page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
