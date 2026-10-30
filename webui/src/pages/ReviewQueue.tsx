import { useState } from "react";
import { loadReviewItems, type ReviewFilters } from "../api/reviewQueue";
import { ErrorState, Loading } from "../components/Feedback";
import { jaReviewQueue as j } from "../i18n/reviewQueue";
import { useLoad } from "../lib/useLoad";
import { number } from "../lib/format";
import { ReviewCard } from "./reviewQueue/ReviewCard";
import "./reviewQueue/reviewQueue.css";

const DEFAULTS: ReviewFilters = { priority: "", status: "", tenant: "", limit: "100" };

export function ReviewQueuePage() {
  const [form, setForm] = useState<ReviewFilters>(DEFAULTS);
  const [applied, setApplied] = useState<ReviewFilters>(DEFAULTS);
  const { data, error, loading, reload } = useLoad(() => loadReviewItems(applied), [applied], j.loadFailed);
  const set = (key: keyof ReviewFilters) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  return (
    <section>
      <h1>{j.title}</h1>
      <p className="muted">{j.intro}</p>
      <form
        className="toolbar rq-filters"
        aria-label={j.filters}
        onSubmit={(e) => {
          e.preventDefault();
          setApplied({ ...form });
        }}
      >
        <label>
          {j.priority}
          <select value={form.priority} onChange={set("priority")}>
            <option value="">{j.all}</option>
            {["high", "medium", "low"].map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label>
          {j.status}
          <select value={form.status} onChange={set("status")}>
            <option value="">{j.all}</option>
            <option value="open">open</option>
          </select>
        </label>
        <label>
          {j.tenant}
          <input value={form.tenant} placeholder={j.tenantPlaceholder} onChange={set("tenant")} />
        </label>
        <label>
          {j.limit}
          <input type="number" min={1} max={500} value={form.limit} onChange={set("limit")} />
        </label>
        <button type="submit" disabled={loading}>
          {j.refresh}
        </button>
      </form>

      {error ? (
        <ErrorState message={error} onRetry={reload} />
      ) : !data ? (
        <Loading />
      ) : data.forbidden ? (
        <p role="note" className="warn">
          {j.forbidden}
        </p>
      ) : (
        <>
          <dl className="rq-summary">
            <div className="kpi">
              <dt>{j.totalLoaded}</dt>
              <dd>{number(data.payload.total_loaded)}</dd>
            </div>
            <div className="kpi">
              <dt>{j.returnedCount}</dt>
              <dd>{number(data.payload.returned_count)}</dd>
            </div>
            <div className="kpi">
              <dt>{j.malformed}</dt>
              <dd>{number(data.payload.skipped_malformed_lines)}</dd>
            </div>
            <div className="kpi">
              <dt>{j.source}</dt>
              <dd className="muted">{data.payload.source_path}</dd>
            </div>
          </dl>
          {data.payload.skipped_malformed_lines > 0 && (
            <p role="note" className="warn">
              {j.malformedWarning(data.payload.skipped_malformed_lines)}
            </p>
          )}
          {data.payload.items.length === 0 ? (
            <p className="empty">{j.empty}</p>
          ) : (
            <ul className="rq-items">
              {data.payload.items.map((item) => (
                <ReviewCard key={`${item.tenant_id}/${item.review_id}`} item={item} />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
