import type { ReactNode } from "react";
import { ErrorState, Loading } from "../../components/Feedback";
import type { Loaded } from "../../lib/useLoad";
import "./overview.css";

interface Props<T> {
  title: ReactNode;
  state: Loaded<T>;
  failureText: string;
  children: (data: T) => ReactNode;
}

/** A panel that shows its own four states (loading, error with retry, content). The empty state is up to the content. */
export function Block<T>({ title, state, failureText, children }: Props<T>) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      {state.error ? (
        <ErrorState message={failureText} onRetry={state.reload} />
      ) : state.data === null ? (
        state.loading && <Loading />
      ) : (
        children(state.data)
      )}
    </section>
  );
}
