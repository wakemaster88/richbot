"use client";

import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  name?: string;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`[ErrorBoundary${this.props.name ? ` ${this.props.name}` : ""}]`, error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div
            className="card p-4 flex flex-col items-center justify-center text-[10px] text-[var(--text-tertiary)] gap-1"
            title={this.state.error?.message}
          >
            <span>Komponente konnte nicht geladen werden</span>
            {typeof window !== "undefined" && process.env.NODE_ENV === "development" && this.state.error && (
              <span className="text-[8px] text-[var(--warn)] truncate max-w-full px-2">
                {this.state.error.message}
              </span>
            )}
          </div>
        )
      );
    }
    return this.props.children;
  }
}
