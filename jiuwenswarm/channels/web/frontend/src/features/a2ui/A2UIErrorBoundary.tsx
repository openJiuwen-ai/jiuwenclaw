// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { Component, type ErrorInfo, type ReactNode } from 'react';
import { a2uiError } from './formDefaults';

interface A2UIErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
  resetKey?: string;
}

interface A2UIErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * Error boundary for A2UI components to prevent page crashes
 * when rendering invalid or missing component references.
 */
export class A2UIErrorBoundary extends Component<
  A2UIErrorBoundaryProps,
  A2UIErrorBoundaryState
> {
  constructor(props: A2UIErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): A2UIErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    a2uiError('[A2UI Error Boundary]', { error, errorInfo });
    this.props.onError?.(error, errorInfo);
  }

  componentDidUpdate(prevProps: A2UIErrorBoundaryProps) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false, error: null });
    }
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <div className="a2ui-error-boundary p-4 border border-danger/30 rounded-lg bg-danger/5">
          <p className="text-danger text-sm font-medium mb-1">
            界面内容暂时无法显示
          </p>
          <p className="text-text-muted text-xs">
            请稍后重试或重新生成结果
          </p>
        </div>
      );
    }

    return this.props.children;
  }
}
