import { Component, type ErrorInfo, type ReactNode } from "react";

// Q2 quick-win: the 3D canvas (three.js/WebGL) is the only part of the UI that
// can hard-crash on browser/GPU limitations. This boundary isolates that
// failure: the rest of the Design page keeps working and the owner gets a
// plain-language explanation with a retry — never a blank page. Minimal by
// contract: class component with componentDidCatch, no dependencies.

type Props = {
  children: ReactNode;
  /**
   * Optional parent hook: alongside the internal state reset the parent can
   * remount the crashed subtree (fresh `key`), which is the only reliable
   * recovery for some WebGL failures.
   */
  onRetry?: () => void;
};

type State = { hasError: boolean };

export class SceneViewerErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Console-only: the owner-facing surface is the fallback below; the
    // diagnostics page reads nothing from here.
    console.error("SceneViewer crashed:", error, info.componentStack);
  }

  private handleRetry = () => {
    this.setState({ hasError: false });
    this.props.onRetry?.();
  };

  render() {
    if (this.state.hasError) {
      return (
        <section className="viewer-error-boundary" role="alert">
          <h3>3D недоступно в этом браузере</h3>
          <p className="muted">
            Работа с планом и фотографиями остаётся доступной.
          </p>
          <button type="button" className="secondary" onClick={this.handleRetry}>
            Повторить
          </button>
        </section>
      );
    }
    return this.props.children;
  }
}
