import { Component } from "react";
import type { ErrorInfo, ReactElement, ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

interface Props {
  place: string; // where the app is; moving elsewhere draws the screen again
  children: ReactNode;
}

interface State {
  error: Error | null;
  place: string; // where the error, if any, happened
}

/** A screen that fails while drawing says so, beside the sidebar, instead of taking
 * the whole window down with it. React offers this only as a class. */
export class ScreenBoundary extends Component<Props, State> {
  state: State = { error: null, place: this.props.place };

  static getDerivedStateFromProps(
    props: Props,
    state: State,
  ): Partial<State> | null {
    return props.place === state.place
      ? null
      : { error: null, place: props.place };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(error, info.componentStack);
  }

  render(): ReactElement {
    const { error } = this.state;
    if (!error) return <>{this.props.children}</>;
    return (
      <main className="main" id="main">
        <header className="topbar">
          <h1>This screen couldn't be shown</h1>
        </header>
        <div className="content">
          <div className="pane">
            <div className="empty" role="alert">
              <span className="empty-icon">
                <AlertTriangle size={20} aria-hidden="true" />
              </span>
              <p>Something on it failed: {error.message}</p>
              <div className="hstack">
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    window.history.back();
                  }}
                >
                  Back
                </button>
                <button
                  type="button"
                  className="btn primary"
                  onClick={() => {
                    this.setState({ error: null });
                  }}
                >
                  Try again
                </button>
              </div>
            </div>
          </div>
        </div>
      </main>
    );
  }
}
