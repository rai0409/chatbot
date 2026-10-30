import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { ApprovalDecision, ApprovalItem, Integration, SystemStatus } from "../api/types";
import { ErrorState, Loading } from "../components/Feedback";
import { reasonText, t } from "../i18n/ja";

// The integrations that have a console switch (the others, llm and oidc, keep the switches they already have).
const SWITCHABLE = new Set(["sharepoint", "outlook_draft", "smtp", "slack"]);

interface Pending {
  integration: Integration;
  enable: boolean;
}

export function SystemStatusPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string>("");
  const [notice, setNotice] = useState<string>("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [actor, setActor] = useState("");
  const [waiting, setWaiting] = useState<ApprovalItem[]>([]);

  const load = useCallback(async () => {
    setError("");
    try {
      setStatus(await api.systemStatus());
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setError(t.system.loadFailed);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // The requests that wait for a second person are read only when there are some (the status says how many).
  const pendingCount = status?.approvals?.pending ?? 0;
  useEffect(() => {
    if (!pendingCount) {
      setWaiting([]);
      return;
    }
    let current = true;
    api
      .approvals("pending")
      .then((res) => current && setWaiting(res.approvals))
      .catch(() => current && setNotice(t.system.approvalsLoadFailed));
    return () => {
      current = false;
    };
  }, [pendingCount, status?.revision]);

  const needsActor = (status?.approvals?.required ?? 1) >= 2;

  async function submit() {
    const asking = pending?.enable === true && needsActor;
    if (!pending || !status || !reason.trim() || (asking && !actor.trim())) return;
    setBusy(true);
    setNotice("");
    try {
      const next = await api.setSwitch({
        name: pending.integration.name,
        enabled: pending.enable,
        reason: reason.trim(),
        expected_rev: status.revision,
        ...(asking ? { actor: actor.trim() } : {}),
      });
      setStatus(next);
      setNotice(next.pending ? t.system.requested : t.system.changed);
      setPending(null);
      setReason("");
    } catch (e) {
      setNotice(e instanceof ApiError && e.status === 409 ? t.system.conflict : t.system.failed);
    } finally {
      setBusy(false);
    }
  }

  async function decide(item: ApprovalItem, decision: ApprovalDecision) {
    if (!actor.trim()) return;
    setBusy(true);
    setNotice("");
    try {
      const next = await api.decideApproval(item.id, decision, actor.trim());
      setStatus(next);
      setNotice({ approve: t.system.approved, reject: t.system.rejected, cancel: t.system.cancelled }[decision]);
    } catch (e) {
      // The server's sentence says why (for example: the person who asked cannot approve their own request).
      setNotice(`${t.system.approvalFailed}${e instanceof ApiError && e.detail ? `: ${e.detail}` : ""}`);
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!status) return <Loading />;

  const allowed = status.environment.external_integrations;
  return (
    <section>
      <h1>{t.system.title}</h1>
      <p className="muted">{t.system.intro}</p>
      <p>
        <strong>{t.system.environment}:</strong> {allowed.length ? allowed.join(", ") : t.system.noneAllowed}
        <span className="muted"> ・ {t.system.revision}: {status.revision}</span>
      </p>
      {status.warnings.length > 0 && (
        <div role="note" className="warn">
          <strong>{t.system.warnings}</strong>
          <ul>
            {status.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      <table>
        <thead>
          <tr>
            <th scope="col">{t.system.colName}</th>
            <th scope="col">{t.system.colState}</th>
            <th scope="col">{t.system.colWhy}</th>
            <th scope="col">{t.system.colAction}</th>
          </tr>
        </thead>
        <tbody>
          {status.integrations.map((item) => (
            <tr key={item.name}>
              <th scope="row">{item.label}</th>
              <td>
                <span className={`badge ${item.state}`}>{t.state[item.state]}</span>
              </td>
              <td>{reasonText(item.reason)}</td>
              <td>
                {SWITCHABLE.has(item.name) ? (
                  <button type="button" disabled={busy} onClick={() => setPending({ integration: item, enable: !item.enabled })}>
                    {item.enabled ? t.system.turnOff : t.system.turnOn}
                  </button>
                ) : (
                  <span className="muted">{t.system.noSwitch}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {waiting.length > 0 && (
        <>
          <h2>{t.system.approvalsTitle}</h2>
          <p className="muted">{t.system.approvalsIntro}</p>
          <label>
            {t.system.approvalActor}
            <input type="text" value={actor} onChange={(e) => setActor(e.target.value)} maxLength={64} />
          </label>
          <table>
            <thead>
              <tr>
                <th scope="col">{t.system.colTarget}</th>
                <th scope="col">{t.system.colRequester}</th>
                <th scope="col">{t.system.colRequestReason}</th>
                <th scope="col">{t.system.colExpires}</th>
                <th scope="col">{t.system.colAction}</th>
              </tr>
            </thead>
            <tbody>
              {waiting.map((item) => (
                <tr key={item.id}>
                  <th scope="row">{status.integrations.find((i) => i.name === item.params.name)?.label ?? item.params.name}</th>
                  <td>
                    {item.requested_by.name}
                    {item.requested_by.verified && <span className="muted"> （{t.system.verified}）</span>}
                  </td>
                  <td>{item.reason}</td>
                  <td>{item.expires_at.slice(0, 16).replace("T", " ")}</td>
                  <td>
                    <button type="button" disabled={busy || !actor.trim()} onClick={() => decide(item, "approve")}>
                      {t.system.approve}
                    </button>
                    <button type="button" disabled={busy || !actor.trim()} onClick={() => decide(item, "reject")}>
                      {t.system.reject}
                    </button>
                    <button type="button" disabled={busy || !actor.trim()} onClick={() => decide(item, "cancel")}>
                      {t.system.cancelRequest}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2>{t.system.llmTitle}</h2>
      {status.llm.profiles_file_problems.length > 0 && (
        <p role="alert" className="error">
          {t.system.problems}: {status.llm.profiles_file_problems.join(" / ")}
        </p>
      )}
      <table>
        <thead>
          <tr>
            <th scope="col">{t.system.colProfile}</th>
            <th scope="col">{t.system.colModel}</th>
            <th scope="col">{t.system.colWhere}</th>
            <th scope="col">{t.system.colBreaker}</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(status.llm.profiles).map(([name, profile]) => (
            <tr key={name}>
              <th scope="row">{name}</th>
              <td>{profile.model || "—"}</td>
              <td>{t.location[profile.location]}</td>
              <td>
                <span className={`badge ${profile.breaker === "open" ? "failing" : "ok"}`}>{t.breaker[profile.breaker]}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {pending && (
        <div role="dialog" aria-modal="true" aria-labelledby="switch-title" className="dialog">
          <h2 id="switch-title">
            {pending.integration.label}: {pending.enable ? t.system.turnOn : t.system.turnOff}
          </h2>
          <label>
            {t.system.reasonLabel}
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} rows={3} />
          </label>
          <p className="muted">{t.system.reasonHelp}</p>
          {pending.enable && needsActor && (
            <>
              <label>
                {t.system.actorLabel}
                <input type="text" value={actor} onChange={(e) => setActor(e.target.value)} maxLength={64} />
              </label>
              <p className="muted">{t.system.actorHelp}</p>
            </>
          )}
          <button type="button" disabled={busy || !reason.trim() || (pending.enable && needsActor && !actor.trim())} onClick={submit}>
            {t.system.confirm}
          </button>
          <button
            type="button"
            onClick={() => {
              setPending(null);
              setReason("");
            }}
          >
            {t.system.cancel}
          </button>
        </div>
      )}
    </section>
  );
}
