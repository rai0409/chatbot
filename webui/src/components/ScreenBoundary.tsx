import { Component, type ErrorInfo, type ReactNode } from "react";
import { t } from "../i18n/ja";

/** True for the failure of loading a screen's chunk: after a new release the old file names are gone from the server. */
export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const text = `${error.name} ${error.message}`;
  return (
    error.name === "ChunkLoadError" ||
    /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Unable to preload CSS|Loading chunk .* failed/i.test(text)
  );
}

interface Props {
  children: ReactNode;
  /** Reloads the page (a test passes its own). */
  onReload?: () => void;
}

interface State {
  error: unknown;
}

/** Catches a screen that cannot be shown: a chunk that cannot be loaded asks for a reload, anything else says it failed. */
export class ScreenBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: unknown): State {
    return { error };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("screen failed", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const reload = this.props.onReload ?? (() => window.location.reload());
    return (
      <div role="alert" className="error screen-error">
        <p>{isChunkLoadError(error) ? t.app.newVersion : t.app.screenFailed}</p>
        <button type="button" onClick={reload}>
          {t.app.reload}
        </button>
      </div>
    );
  }
}
