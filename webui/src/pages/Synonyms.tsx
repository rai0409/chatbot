import { useState } from "react";
import { ApiError } from "../api/client";
import { applyApproved, decideCandidate, loadCandidates, type CandidateStatus } from "../api/synonyms";
import { ErrorState, Loading } from "../components/Feedback";
import { jaSynonyms as j } from "../i18n/synonyms";
import { useLoad } from "../lib/useLoad";
import { ConfirmDialog } from "./reviewQueue/ConfirmDialog";
import "./synonyms/synonyms.css";

const DECISIONS: Array<[string, CandidateStatus]> = [
  [j.approve, "approved"],
  [j.reject, "rejected"],
  [j.hold, "candidate"],
];

export function SynonymsPage() {
  const [status, setStatus] = useState("candidate");
  const [applied, setApplied] = useState({ status: "candidate", token: "" });
  const [token, setToken] = useState("");
  const [profile, setProfile] = useState("default");
  const [busy, setBusy] = useState(false);
  const [confirmApply, setConfirmApply] = useState(false);
  const [forbidden, setForbidden] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const { data, error, loading, reload } = useLoad(() => loadCandidates(applied.status, applied.token), [applied], j.loadFailed);
  const noRights = forbidden || data?.forbidden === true;

  function fail(prefix: string, e: unknown) {
    if (e instanceof ApiError) {
      if (e.status === 401) return;
      if (e.status === 403) return setForbidden(true);
      if (e.status === 404) return setMessage({ ok: false, text: j.failedWith(j.notFound, e.status) });
      if (e.status === 400 && prefix === j.applyFailed) return setMessage({ ok: false, text: j.failedWith(j.invalidProfile, e.status) });
      return setMessage({ ok: false, text: j.failedWith(prefix, e.status) });
    }
    setMessage({ ok: false, text: prefix });
  }

  async function decide(id: string, next: CandidateStatus) {
    setBusy(true);
    setMessage(null);
    try {
      await decideCandidate(id, next, token);
      setMessage({ ok: true, text: j.updated });
      reload();
    } catch (e) {
      fail(j.updateFailed, e);
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    setConfirmApply(false);
    setBusy(true);
    setMessage(null);
    try {
      const result = await applyApproved(profile, token);
      setMessage({ ok: true, text: j.applied(result.entries_before, result.entries_after) });
    } catch (e) {
      fail(j.applyFailed, e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>{j.title}</h1>
      <p className="muted">{j.intro}</p>
      {noRights && (
        <p role="note" className="warn">
          {j.forbidden}
        </p>
      )}
      <form
        className="toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          setForbidden(false);
          setApplied({ status, token });
        }}
      >
        <label>
          {j.token}
          <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
        </label>
        <label>
          {j.status}
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {["candidate", "approved", "rejected", ""].map((s) => (
              <option key={s} value={s}>
                {j.statusOptions[s]}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={loading}>
          {j.reload}
        </button>
      </form>
      <div className="toolbar">
        <label>
          {j.profile}
          <input value={profile} maxLength={64} size={16} onChange={(e) => setProfile(e.target.value)} />
        </label>
        <button type="button" disabled={busy || noRights || !profile.trim()} onClick={() => setConfirmApply(true)}>
          {busy ? j.applying : j.apply}
        </button>
      </div>
      {message && (
        <p role={message.ok ? "status" : "alert"} className={message.ok ? "notice" : "error"}>
          {message.text}
        </p>
      )}

      {error ? (
        <ErrorState message={error} onRetry={reload} />
      ) : !data ? (
        <Loading />
      ) : data.forbidden ? null : (
        <>
          <p>
            {j.count(data.list.total)}
            {Object.entries(data.list.counts).map(([key, count]) => (
              <span key={key} className="badge syn-count">
                {j.statusOptions[key] ?? key}: {count}
              </span>
            ))}
          </p>
          {data.list.items.length === 0 ? (
            <p className="empty">{j.empty}</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th scope="col">{j.colCanonical}</th>
                  <th scope="col">{j.colSynonyms}</th>
                  <th scope="col">{j.colMethod}</th>
                  <th scope="col">{j.colStatus}</th>
                  <th scope="col">{j.colActions}</th>
                </tr>
              </thead>
              <tbody>
                {data.list.items.map((item) => (
                  <tr key={item.candidate_id}>
                    <th scope="row">{item.canonical || ""}</th>
                    <td>{(item.synonyms || []).join(j.separator)}</td>
                    <td>{item.method || ""}</td>
                    <td>
                      <span className={`badge syn-${item.status}`}>{j.statusOptions[item.status] ?? item.status}</span>
                    </td>
                    <td className="syn-actions">
                      {DECISIONS.map(([label, value]) => (
                        <button
                          key={value}
                          type="button"
                          disabled={busy || noRights}
                          aria-label={`${label}: ${item.canonical || item.candidate_id}`}
                          onClick={() => void decide(item.candidate_id, value)}
                        >
                          {label}
                        </button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
      {confirmApply && (
        <ConfirmDialog
          title={j.applyTitle}
          body={j.applyBody(profile.trim())}
          confirmLabel={j.confirm}
          cancelLabel={j.cancel}
          busy={busy}
          onConfirm={() => void apply()}
          onCancel={() => setConfirmApply(false)}
        />
      )}
    </section>
  );
}
