import { t } from "../i18n/ja";

export function Loading() {
  return (
    <p role="status" className="muted">
      {t.app.loading}
    </p>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="error">
      <p>{message}</p>
      <button type="button" onClick={onRetry}>
        {t.app.retry}
      </button>
    </div>
  );
}
