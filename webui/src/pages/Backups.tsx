import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { type BackupJob, backupsApi } from "../api/backups";
import { ReasonActorDialog } from "../components/ReasonActorDialog";
import { ErrorState, Loading } from "../components/Feedback";
import { jaBackups as b } from "../i18n/backups";
import { dateTime, number } from "../lib/format";
import { useLoad } from "../lib/useLoad";

const POLL_MS = 2000;

function size(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${number(Math.max(1, Math.round(bytes / 1024)))} KB`;
}

export function BackupsPage() {
  const list = useLoad(backupsApi.list, [], b.loadFailed);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState<BackupJob | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const runningId = jobId || list.data?.running || "";

  // While a backup runs, its status is read every few seconds; the list is reloaded when it ends.
  useEffect(() => {
    if (!runningId) return;
    let stopped = false;
    const tick = async () => {
      try {
        const current = await backupsApi.job(runningId);
        if (stopped) return;
        setJob(current);
        if (current.status !== "running") {
          setJobId("");
          list.reload();
          setNotice({ ok: current.status === "ok", text: current.status === "ok" ? b.statusOk : b.statusError });
          return;
        }
      } catch {
        /* the next tick tries again */
      }
      if (!stopped) timer = window.setTimeout(tick, POLL_MS);
    };
    let timer = window.setTimeout(tick, 0);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningId]);

  async function start(_reason: string, actor: string) {
    setBusy(true);
    try {
      const res = await backupsApi.start(actor);
      setNotice({ ok: true, text: b.started });
      setJobId(res.job_id);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) {
        setNotice({ ok: false, text: e instanceof ApiError && e.status === 409 ? b.busy : e instanceof ApiError && e.status === 403 ? b.forbidden : e instanceof ApiError && e.status === 501 ? b.unavailable : b.failedStart });
      }
    } finally {
      setBusy(false);
      setAsking(false);
    }
  }

  const data = list.data;
  return (
    <section>
      <h1>{b.title}</h1>
      <p>{b.intro}</p>
      <p className="muted">{b.externalNote}</p>
      {list.error ? (
        <ErrorState message={list.error} onRetry={list.reload} />
      ) : !data ? (
        <Loading />
      ) : (
        <>
          {!data.available && (
            <p role="note" className="warn">
              {b.unavailable}
            </p>
          )}
          <div className="toolbar">
            <button type="button" disabled={busy || !!runningId || !data.available} onClick={() => setAsking(true)}>
              {b.start}
            </button>
            {runningId && <span role="status">{b.running}</span>}
          </div>
          {notice && (
            <p role={notice.ok ? "status" : "alert"} className={notice.ok ? "notice" : "error"}>
              {notice.text}
            </p>
          )}
          {job && job.output && (
            <details open={job.status === "error"}>
              <summary>{b.output}</summary>
              <pre style={{ whiteSpace: "pre-wrap", overflowX: "auto" }}>{job.output}</pre>
            </details>
          )}
          <section className="panel" aria-labelledby="bk-list">
            <h2 id="bk-list">{b.archives}</h2>
            {data.archives.length === 0 ? (
              <p className="empty">{b.empty}</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th scope="col">{b.colName}</th>
                    <th scope="col">{b.colSize}</th>
                    <th scope="col">{b.colModified}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.archives.map((a) => (
                    <tr key={a.name}>
                      <th scope="row">{a.name}</th>
                      <td>{size(a.bytes)}</td>
                      <td>{dateTime(new Date(a.modified * 1000).toISOString())}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          <section className="panel" aria-labelledby="bk-restore">
            <h2 id="bk-restore">{b.restoreTitle}</h2>
            <p>{b.restoreBody}</p>
          </section>
        </>
      )}
      {asking && (
        <ReasonActorDialog
          title={b.startDialog}
          body={b.startBody}
          confirmLabel={b.confirm}
          cancelLabel={b.cancel}
          reasonLabel=""
          actorLabel={b.actorLabel}
          needBoth={b.needActor}
          busy={busy}
          reasonOptional
          onConfirm={(r, a) => void start(r, a)}
          onCancel={() => setAsking(false)}
        />
      )}
    </section>
  );
}
