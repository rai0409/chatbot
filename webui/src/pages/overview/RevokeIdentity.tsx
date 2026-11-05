import { useState, type FormEvent } from "react";
import { ApiError } from "../../api/client";
import { overviewApi } from "../../api/overview";
import { jaOverview } from "../../i18n/overview";

const r = jaOverview.revoke;
const VALID = /^[A-Za-z0-9_-]{6,64}$/;

/** Ends the single-sign-on sessions of one person. Only the installation administrator sees it (the server checks too). */
export function RevokeIdentity() {
  const [value, setValue] = useState("");
  const [problem, setProblem] = useState("");
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  function ask(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    if (!VALID.test(value.trim())) {
      setProblem(r.invalid);
      return;
    }
    setProblem("");
    setAsking(true);
  }

  async function revoke() {
    const id = value.trim();
    setBusy(true);
    try {
      await overviewApi.revokeIdentity(id);
      setNotice({ ok: true, text: r.done(id) });
      setValue("");
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setNotice({ ok: false, text: e instanceof ApiError && e.status === 403 ? r.forbidden : r.failed });
    } finally {
      setBusy(false);
      setAsking(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="revoke-title">
      <h2 id="revoke-title">{r.title}</h2>
      <p className="muted">{r.help}</p>
      <form className="toolbar" onSubmit={ask}>
        <label>
          {r.label}
          <input value={value} onChange={(e) => setValue(e.target.value)} maxLength={64} size={30} aria-invalid={problem ? true : undefined} />
        </label>
        <button type="submit" disabled={busy}>
          {r.ask}
        </button>
      </form>
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      {notice && (
        <p role={notice.ok ? "status" : "alert"} className={notice.ok ? "notice" : "error"}>
          {notice.text}
        </p>
      )}
      {asking && (
        <div role="dialog" aria-modal="true" aria-labelledby="revoke-dialog-title" className="dialog">
          <h2 id="revoke-dialog-title">{r.title}</h2>
          <p>{r.confirm(value.trim())}</p>
          <button type="button" disabled={busy} onClick={revoke}>
            {r.ask}
          </button>
          <button type="button" disabled={busy} onClick={() => setAsking(false)}>
            {jaOverview.common.cancel}
          </button>
        </div>
      )}
    </section>
  );
}
