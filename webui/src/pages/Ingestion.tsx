import { type DragEvent, type KeyboardEvent, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { type IngestionJob, type Report, ingestionApi } from "../api/ingestion";
import { ErrorState, Loading } from "../components/Feedback";
import { ji } from "../i18n/ingestion";
import { dateTime } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import { JobView } from "./ingestion/JobView";
import { ConfirmDialog, type Outcome, ResultView, StatusBadge, failureMessage, lines } from "./ingestion/parts";

const ALLOWED = /\.(pdf|docx|xlsx|csv|pptx)$/i;

interface Uploaded {
  id: number;
  name: string;
  state: "busy" | "ok" | "ng";
  message: string;
  path: string | null;
}

/** Sections 1 and 2: choose documents (upload or server paths), the target, and run the check or the ingestion. */
function RawSection({ onChanged }: { onChanged: () => void }) {
  const [files, setFiles] = useState<Uploaded[]>([]);
  const [paths, setPaths] = useState("");
  const [tenant, setTenant] = useState("");
  const [collection, setCollection] = useState("");
  const [execute, setExecute] = useState(false);
  const [asyncJob, setAsyncJob] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [jobId, setJobId] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const nextId = useRef(1);

  function patch(id: number, change: Partial<Uploaded>) {
    setFiles((list) => list.map((f) => (f.id === id ? { ...f, ...change } : f)));
  }

  async function upload(file: File) {
    const id = nextId.current++;
    if (!ALLOWED.test(file.name)) {
      setFiles((list) => [...list, { id, name: file.name, state: "ng", message: ji.files.unsupported, path: null }]);
      return;
    }
    setFiles((list) => [...list, { id, name: file.name, state: "busy", message: ji.files.uploading, path: null }]);
    try {
      const res = await ingestionApi.upload(file, tenant.trim() || "default");
      patch(id, { state: "ok", message: `${Math.round(res.bytes / 1024)} KB`, path: res.path });
    } catch (e) {
      patch(id, { state: "ng", message: e instanceof ApiError && e.status === 413 ? ji.files.tooLarge : failureMessage(e, ji.common.failed) });
    }
  }

  function take(list: FileList | null) {
    Array.from(list ?? []).forEach((f) => void upload(f));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    take(e.dataTransfer.files);
  }

  function onDropKey(e: KeyboardEvent) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      picker.current?.click();
    }
  }

  function start() {
    const inputs = [...files.filter((f) => f.path).map((f) => f.path as string), ...lines(paths)];
    if (!inputs.length) return setOutcome({ error: ji.raw.noInputs });
    if (!tenant.trim()) return setOutcome({ error: ji.raw.needTenant });
    if (!collection.trim()) return setOutcome({ error: ji.raw.needCollection });
    if (execute) setConfirming(true);
    else void run();
  }

  async function run() {
    setConfirming(false);
    const inputs = [...files.filter((f) => f.path).map((f) => f.path as string), ...lines(paths)];
    setBusy(true);
    setJobId("");
    try {
      const res: Report = await ingestionApi.rawDocuments({ inputs, expected_tenant: tenant.trim(), collection: collection.trim(), execute, async_job: asyncJob });
      setOutcome({ data: res });
      if (res.async && typeof res.job_id === "string") setJobId(res.job_id);
    } catch (e) {
      setOutcome({ error: failureMessage(e, ji.common.requestFailed) });
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  return (
    <>
      <section className="panel" aria-labelledby="h-files">
        <h2 id="h-files">{ji.files.heading}</h2>
        <p className="muted">{ji.files.note}</p>
        <div
          className="drop"
          tabIndex={0}
          role="button"
          aria-label={ji.files.dropLabel}
          onClick={() => picker.current?.click()}
          onKeyDown={onDropKey}
          onDragEnter={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
          style={{ border: "2px dashed var(--line)", borderRadius: 12, padding: 24, textAlign: "center", cursor: "pointer", background: over ? "var(--accent-weak, transparent)" : undefined }}
        >
          {ji.files.drop}
        </div>
        <input
          ref={picker}
          type="file"
          multiple
          accept=".pdf,.docx,.xlsx,.csv,.pptx"
          hidden
          aria-label={ji.files.pickLabel}
          onChange={(e) => {
            take(e.target.files);
            e.target.value = "";
          }}
        />
        {files.length > 0 && (
          <ul aria-label={ji.files.listLabel} style={{ listStyle: "none", padding: 0 }}>
            {files.map((f) => (
              <li key={f.id} className="toolbar">
                <span>{f.name}</span>
                <span className={f.state === "ng" ? "error" : f.state === "ok" ? "notice" : "muted"} role={f.state === "ng" ? "alert" : undefined}>
                  {f.message}
                </span>
                <button type="button" aria-label={`${ji.files.remove}: ${f.name}`} onClick={() => setFiles((list) => list.filter((x) => x.id !== f.id))}>
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        <label style={{ display: "block", marginTop: 12 }}>
          {ji.files.paths}
          <textarea value={paths} onChange={(e) => setPaths(e.target.value)} placeholder="eval/cases/sample_docs/sample_manual.docx" rows={3} style={{ width: "100%" }} />
        </label>
      </section>

      <section className="panel" aria-labelledby="h-raw">
        <h2 id="h-raw">{ji.raw.heading}</h2>
        <div className="toolbar">
          <label>
            {ji.raw.tenant}
            <input type="text" value={tenant} onChange={(e) => setTenant(e.target.value)} placeholder="pilot_tenant_a" />
          </label>
          <label>
            {ji.raw.collection}
            <input type="text" value={collection} onChange={(e) => setCollection(e.target.value)} placeholder="pilot_staging_v1" />
          </label>
        </div>
        <p>
          <label>
            <input type="checkbox" checked={execute} onChange={(e) => setExecute(e.target.checked)} /> {ji.raw.execute}
          </label>
        </p>
        <p>
          <label>
            <input type="checkbox" checked={asyncJob} onChange={(e) => setAsyncJob(e.target.checked)} /> {ji.raw.async}
          </label>
        </p>
        <p className="warn">{ji.raw.note}</p>
        <button type="button" disabled={busy} onClick={start}>
          {busy ? ji.common.busy : ji.raw.run}
        </button>
        {jobId ? <JobView jobId={jobId} initial={outcome} onSettled={onChanged} /> : <ResultView outcome={outcome} label={ji.raw.resultLabel} />}
      </section>
      {confirming && <ConfirmDialog title={ji.raw.confirmTitle} busy={busy} onConfirm={() => void run()} onCancel={() => setConfirming(false)} />}
    </>
  );
}

type Pending = { kind: "promote" | "delete"; title: string };

/** Section 3: promote a staging collection to the served one, and delete documents. Every action has a check (no change) and a run. */
function PromoteDeleteSection({ onChanged }: { onChanged: () => void }) {
  const [pTenant, setPTenant] = useState("");
  const [pCollection, setPCollection] = useState("");
  const [pOutcome, setPOutcome] = useState<Outcome | null>(null);
  const [dTenant, setDTenant] = useState("");
  const [dCollection, setDCollection] = useState("");
  const [dDocs, setDDocs] = useState("");
  const [dOutcome, setDOutcome] = useState<Outcome | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);

  async function promote(execute: boolean) {
    setPending(null);
    setBusy(true);
    try {
      setPOutcome({ data: await ingestionApi.promote({ tenant_id: pTenant.trim(), collection: pCollection.trim(), execute }) });
    } catch (e) {
      setPOutcome({ error: failureMessage(e, ji.common.failed) });
    } finally {
      setBusy(false);
    }
  }

  async function remove(execute: boolean) {
    setPending(null);
    setBusy(true);
    try {
      setDOutcome({
        data: await ingestionApi.deleteDocuments({ tenant_id: dTenant.trim(), source_docs: lines(dDocs), collection: dCollection.trim() || null, execute }),
      });
    } catch (e) {
      setDOutcome({ error: failureMessage(e, ji.common.failed) });
    } finally {
      setBusy(false);
    }
  }

  function askPromote(execute: boolean) {
    if (!pTenant.trim() || !pCollection.trim()) return setPOutcome({ error: ji.promote.needBoth });
    if (execute) setPending({ kind: "promote", title: ji.promote.confirm(pCollection.trim(), pTenant.trim()) });
    else void promote(false);
  }

  function askRemove(execute: boolean) {
    const docs = lines(dDocs);
    if (!dTenant.trim()) return setDOutcome({ error: ji.remove.needTenant });
    if (!docs.length) return setDOutcome({ error: ji.remove.needDocs });
    if (execute) setPending({ kind: "delete", title: ji.remove.confirm(docs.length, dCollection.trim() || ji.remove.production) });
    else void remove(false);
  }

  return (
    <section className="panel" aria-labelledby="h-promote">
      <h2 id="h-promote">{ji.promote.heading}</h2>
      <p className="muted">{ji.promote.note}</p>
      <div className="toolbar">
        <label>
          {ji.promote.tenant}
          <input type="text" value={pTenant} onChange={(e) => setPTenant(e.target.value)} placeholder="pilot_tenant_a" />
        </label>
        <label>
          {ji.promote.collection}
          <input type="text" value={pCollection} onChange={(e) => setPCollection(e.target.value)} placeholder="pilot_staging_v1" />
        </label>
      </div>
      <div className="toolbar">
        <button type="button" disabled={busy} onClick={() => askPromote(false)}>
          {ji.promote.check}
        </button>
        <button type="button" disabled={busy} onClick={() => askPromote(true)}>
          {ji.promote.run}
        </button>
      </div>
      <ResultView outcome={pOutcome} label={ji.promote.resultLabel} />

      <p className="muted" style={{ marginTop: 16 }}>
        {ji.remove.note}
      </p>
      <div className="toolbar">
        <label>
          {ji.remove.tenant}
          <input type="text" value={dTenant} onChange={(e) => setDTenant(e.target.value)} placeholder="pilot_tenant_a" />
        </label>
        <label>
          {ji.remove.collection}
          <input type="text" value={dCollection} onChange={(e) => setDCollection(e.target.value)} />
        </label>
      </div>
      <label style={{ display: "block" }}>
        {ji.remove.docs}
        <textarea value={dDocs} onChange={(e) => setDDocs(e.target.value)} rows={3} style={{ width: "100%" }} />
      </label>
      <div className="toolbar">
        <button type="button" disabled={busy} onClick={() => askRemove(false)}>
          {ji.remove.check}
        </button>
        <button type="button" disabled={busy} onClick={() => askRemove(true)}>
          {ji.remove.run}
        </button>
      </div>
      <ResultView outcome={dOutcome} label={ji.remove.resultLabel} />
      <p className="warn">{ji.remove.warning}</p>
      {pending && (
        <ConfirmDialog
          title={pending.title}
          busy={busy}
          onConfirm={() => {
            void (pending.kind === "promote" ? promote(true) : remove(true)).then(onChanged);
          }}
          onCancel={() => setPending(null)}
        />
      )}
    </section>
  );
}

/** The dry-run check of normalized chunk JSONL files (changes nothing). */
function DryRunSection({ onChanged }: { onChanged: () => void }) {
  const [inputs, setInputs] = useState("");
  const [tenant, setTenant] = useState("");
  const [collection, setCollection] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    const list = lines(inputs);
    if (!list.length) return setOutcome({ error: ji.dry.noInputs });
    setBusy(true);
    try {
      setOutcome({ data: await ingestionApi.dryRun({ inputs: list, expected_tenant: tenant.trim() || null, collection: collection.trim() || null }) });
    } catch (e) {
      setOutcome({ error: failureMessage(e, ji.common.requestFailed) });
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  return (
    <section className="panel" aria-labelledby="h-dry">
      <h2 id="h-dry">{ji.dry.heading}</h2>
      <p className="muted">
        {ji.dry.note}
        <strong>{ji.dry.noteStrong}</strong>
        {ji.dry.noteEnd}
      </p>
      <label style={{ display: "block" }}>
        {ji.dry.inputs}
        <textarea value={inputs} onChange={(e) => setInputs(e.target.value)} placeholder="eval/cases/manufacturing_pilot/manufacturing_chunks.jsonl" rows={3} style={{ width: "100%" }} />
      </label>
      <div className="toolbar">
        <label>
          {ji.dry.tenant}
          <input type="text" value={tenant} onChange={(e) => setTenant(e.target.value)} placeholder="pilot_tenant_a" />
        </label>
        <label>
          {ji.dry.collection}
          <input type="text" value={collection} onChange={(e) => setCollection(e.target.value)} placeholder="pilot_collection_v1" />
        </label>
      </div>
      <button type="button" disabled={busy} onClick={() => void run()}>
        {busy ? ji.common.busy : ji.dry.run}
      </button>
      <ResultView outcome={outcome} label={ji.dry.resultLabel} />
    </section>
  );
}

function createdAt(job: IngestionJob): string {
  return job.created_at ? dateTime(new Date(Number(job.created_at) * 1000).toISOString()) : "—";
}

export function IngestionPage() {
  const jobs = useLoad(() => ingestionApi.jobs().then((r) => r.jobs.slice(0, 50)), [], ji.jobs.loadFailed);
  const [open, setOpen] = useState("");
  const reload = jobs.reload;

  return (
    <section>
      <h1>{ji.title}</h1>
      <RawSection onChanged={reload} />
      <PromoteDeleteSection onChanged={reload} />
      <DryRunSection onChanged={reload} />
      <section className="panel" aria-labelledby="h-jobs">
        <h2 id="h-jobs">{ji.jobs.heading}</h2>
        <div className="toolbar">
          <button type="button" disabled={jobs.loading} onClick={reload}>
            {ji.jobs.reload}
          </button>
        </div>
        {jobs.error ? (
          <ErrorState message={jobs.error} onRetry={reload} />
        ) : !jobs.data ? (
          <Loading />
        ) : jobs.data.length === 0 ? (
          <p className="empty">{ji.jobs.empty}</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">{ji.jobs.colJob}</th>
                  <th scope="col">{ji.jobs.colStatus}</th>
                  <th scope="col">{ji.jobs.colMode}</th>
                  <th scope="col">{ji.jobs.colTenant}</th>
                  <th scope="col">{ji.jobs.colCollection}</th>
                  <th scope="col">{ji.jobs.colCreated}</th>
                  <th scope="col">{ji.jobs.colAction}</th>
                </tr>
              </thead>
              <tbody>
                {jobs.data.map((job) => (
                  <tr key={job.job_id}>
                    <th scope="row">{String(job.job_id).slice(0, 8)}</th>
                    <td>
                      <StatusBadge status={job.status} />
                    </td>
                    <td>{job.mode ? ji.mode[job.mode] ?? job.mode : ""}</td>
                    <td>{job.expected_tenant ?? ""}</td>
                    <td>{job.collection ?? ""}</td>
                    <td>{createdAt(job)}</td>
                    <td>
                      <button type="button" aria-label={`${ji.jobs.show}: ${String(job.job_id).slice(0, 8)}`} onClick={() => setOpen(job.job_id)}>
                        {ji.jobs.show}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {open && <JobView key={open} jobId={open} onSettled={reload} onClose={() => setOpen("")} />}
      </section>
    </section>
  );
}
