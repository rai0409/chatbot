import { useState } from "react";
import { PAGE_SIZE, recordsApi, type RecordFilters } from "../../api/records";
import { ErrorState, Loading } from "../../components/Feedback";
import { jaRecords } from "../../i18n/records";
import { dateTime } from "../../lib/format";
import { useLoad } from "../../lib/useLoad";
import { RecordDetail, modeText } from "./RecordDetail";

const r = jaRecords;
const DAYS = [1, 7, 30, 90];
const INITIAL: RecordFilters = { days: 7, tenant_id: "", answer_mode: "", verdict: "", feedback: "", reviewed: "all", abstained: false, text: "" };

function cut(text: string | undefined, length = 40): string {
  if (!text) return r.list.noText;
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

export function RecordList() {
  const [draft, setDraft] = useState<RecordFilters>(INITIAL);
  const [filters, setFilters] = useState<RecordFilters>(INITIAL);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState("");
  const { data, error, loading, reload } = useLoad(() => recordsApi.list(filters, offset), [filters, offset], r.common.loadFailed);

  function set<K extends keyof RecordFilters>(key: K, value: RecordFilters[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  function apply(event: React.FormEvent) {
    event.preventDefault();
    setOffset(0);
    setFilters(draft);
  }

  const total = data?.total ?? 0;
  return (
    <div>
      <p className="muted">{r.list.auditNote}</p>
      <form className="toolbar" onSubmit={apply} aria-label={r.list.filters}>
        <label>
          {r.list.days}
          <select value={draft.days} onChange={(e) => set("days", Number(e.target.value))}>
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {r.list.daysOption(d)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {r.list.tenant}
          <input value={draft.tenant_id} onChange={(e) => set("tenant_id", e.target.value)} />
        </label>
        <label>
          {r.list.answerMode}
          <select value={draft.answer_mode} onChange={(e) => set("answer_mode", e.target.value)}>
            <option value="">{r.list.anyMode}</option>
            {Object.entries(r.mode).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          {r.list.verdict}
          <select value={draft.verdict} onChange={(e) => set("verdict", e.target.value)}>
            <option value="">{r.list.anyVerdict}</option>
            {Object.entries(r.verdict).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          {r.list.feedback}
          <select value={draft.feedback} onChange={(e) => set("feedback", e.target.value)}>
            <option value="">{r.list.anyFeedback}</option>
            {Object.entries(r.feedbackType).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          {r.list.reviewed}
          <select value={draft.reviewed} onChange={(e) => set("reviewed", e.target.value as RecordFilters["reviewed"])}>
            <option value="all">{r.list.reviewedAll}</option>
            <option value="yes">{r.list.reviewedYes}</option>
            <option value="no">{r.list.reviewedNo}</option>
          </select>
        </label>
        <label>
          {r.list.text}
          <input value={draft.text} onChange={(e) => set("text", e.target.value)} />
        </label>
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={draft.abstained} onChange={(e) => set("abstained", e.target.checked)} />
          {r.list.abstained}
        </label>
        <button type="submit">{r.list.apply}</button>
      </form>

      <div className={`rec-split${selected ? " open" : ""}`}>
        <div>
          {loading && !data && <Loading />}
          {error && <ErrorState message={error} onRetry={reload} />}
          {data && !error && data.records.length === 0 && <p className="empty">{r.list.empty}</p>}
          {data && data.records.length > 0 && (
            <>
              <p className="muted" role="status">
                {r.list.range(offset + 1, offset + data.records.length, total)}
              </p>
              <table className="rec-table">
                <thead>
                  <tr>
                    <th scope="col">{r.list.colTime}</th>
                    <th scope="col">{r.list.colTenant}</th>
                    <th scope="col">{r.list.colQuestion}</th>
                    <th scope="col">{r.list.colMode}</th>
                    <th scope="col">{r.list.colFeedback}</th>
                    <th scope="col">{r.list.colVerdict}</th>
                    <th scope="col">{r.list.colOpen}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.records.map((row) => (
                    <tr key={row.id} aria-selected={row.id === selected}>
                      <td>{dateTime(row.t)}</td>
                      <td>{row.tenant_id}</td>
                      <th scope="row" className="q" style={{ fontWeight: "normal" }}>
                        {cut(row.question)}
                      </th>
                      <td>
                        <span className="badge">{modeText(row.answer_mode)}</span>{" "}
                        {row.degraded && (
                          <span className="badge failing" title={r.list.degradedHint}>
                            {r.list.degraded}
                          </span>
                        )}
                      </td>
                      <td>{row.feedback?.type ? (r.feedbackType[row.feedback.type] ?? row.feedback.type) : r.list.noFeedback}</td>
                      <td>{row.review ? (r.verdict[row.review.verdict] ?? row.review.verdict) : r.list.unreviewed}</td>
                      <td>
                        <button type="button" aria-label={r.list.openFor(row.id)} aria-pressed={row.id === selected} onClick={() => setSelected(row.id)}>
                          {r.list.open}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="rec-pager">
                <button type="button" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                  {r.list.prev}
                </button>
                <button type="button" disabled={offset + PAGE_SIZE >= total || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>
                  {r.list.next}
                </button>
                <span className="muted">{r.list.total(total)}</span>
              </div>
            </>
          )}
        </div>
        {selected && <RecordDetail key={selected} id={selected} onClose={() => setSelected("")} onChanged={reload} />}
      </div>
    </div>
  );
}
