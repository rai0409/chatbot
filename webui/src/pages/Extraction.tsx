import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { extractionApi, type ItemFilter, type LineRef, type PageLine, type ReviewItem } from "../api/extraction";
import { ErrorState, Loading } from "../components/Feedback";
import { jaExtraction as x } from "../i18n/extraction";
import { dateTime, number } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import { ConfirmDialog } from "./extraction/ConfirmDialog";
import { LineEditor } from "./extraction/LineEditor";
import { PageViewer } from "./extraction/PageViewer";
import "./extraction/extraction.css";

const ITEM_LIMIT = 100; // the server's default limit; the legacy page did not pass one
const FIRST: ItemFilter = { tenant: "default", maxConfidence: "", includeReviewed: false, sourceDoc: "" };

type Dialog = { kind: "revert" } | { kind: "reingest"; doc: string };
interface Notice {
  ok: boolean;
  text: string;
}

function confidenceText(value: number | null): string {
  return value == null ? x.noConfidence : value.toFixed(1);
}

function failure(base: string, e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 409) return x.conflict;
    if (e.status === 403) return x.forbidden;
    if (e.status === 404) return x.notFound;
    if (e.status === 400 && e.detail) return x.failedWith(base, e.detail);
  }
  return base;
}

export function ExtractionPage() {
  const [tenantInput, setTenantInput] = useState(FIRST.tenant);
  const [maxInput, setMaxInput] = useState("");
  const [reviewedInput, setReviewedInput] = useState(false);
  const [formProblem, setFormProblem] = useState("");
  const [filter, setFilter] = useState<ItemFilter>(FIRST);
  const [target, setTarget] = useState<LineRef | null>(null);
  const [editing, setEditing] = useState(false);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const focusIndex = useRef<number | null>(null);
  const tableRef = useRef<HTMLTableElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const tenant = filter.tenant;
  const docs = useLoad(() => extractionApi.docs(tenant), [tenant], x.docsLoadFailed);
  const items = useLoad(
    () => extractionApi.items(filter),
    [filter.tenant, filter.maxConfidence, filter.includeReviewed, filter.sourceDoc],
    x.itemsLoadFailed,
  );
  const pageLoad = useLoad(
    () => (target ? extractionApi.page(tenant, target.source_doc, target.page) : Promise.resolve(null)),
    [tenant, target?.source_doc, target?.page],
    x.pageLoadFailed,
  );

  // After a decision the list is reloaded: put the focus on the row that took the place of the decided one.
  useEffect(() => {
    if (focusIndex.current === null || !items.data) return;
    const buttons = tableRef.current?.querySelectorAll<HTMLButtonElement>("button[data-pick]");
    const index = focusIndex.current;
    focusIndex.current = null;
    if (buttons && buttons.length > 0) buttons[Math.min(index, buttons.length - 1)].focus();
    else headingRef.current?.focus();
  }, [items.data]);

  function apply(event: FormEvent) {
    event.preventDefault();
    const max = maxInput.trim();
    if (max && !(Number.isFinite(Number(max)) && Number(max) >= 0 && Number(max) <= 100)) {
      setFormProblem(x.maxConfidenceInvalid);
      return;
    }
    setFormProblem("");
    setNotice(null);
    const next = { tenant: tenantInput.trim() || "default", maxConfidence: max, includeReviewed: reviewedInput, sourceDoc: filter.sourceDoc };
    if (next.tenant !== filter.tenant) {
      setTarget(null);
      setEditing(false);
      next.sourceDoc = "";
    }
    // The same filter does not change any dependency, so a second press of the button loads again explicitly.
    if (JSON.stringify(next) === JSON.stringify(filter)) {
      items.reload();
      docs.reload();
    } else setFilter(next);
  }

  function pick(ref: LineRef, event: { currentTarget: HTMLElement }) {
    trigger.current = event.currentTarget;
    setNotice(null);
    setTarget(ref);
    setEditing(true);
  }

  function closeEditor() {
    setEditing(false);
    const el = trigger.current;
    if (el && el.isConnected) el.focus();
  }

  function closePage() {
    setEditing(false);
    setTarget(null);
    const el = trigger.current;
    if (el && el.isConnected) el.focus();
  }

  function showDoc(doc: string, event: { currentTarget: HTMLElement }) {
    trigger.current = event.currentTarget;
    setFilter({ ...filter, sourceDoc: doc });
  }

  async function decide(run: () => Promise<string>, base: string) {
    if (!target) return;
    setBusy(true);
    setNotice(null);
    try {
      const text = await run();
      setNotice({ ok: true, text });
      const at = items.data?.findIndex((i) => i.source_doc === target.source_doc && i.page === target.page && i.line_id === target.line_id) ?? -1;
      focusIndex.current = at >= 0 ? at : 0;
      setEditing(false);
      items.reload();
      docs.reload();
      pageLoad.reload();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setNotice({ ok: false, text: failure(base, e) });
    } finally {
      setBusy(false);
      setDialog(null);
    }
  }

  const save = (text: string) =>
    decide(async () => {
      await extractionApi.correct(tenant, target as LineRef, text);
      return x.saved;
    }, x.saveFailed);
  const confirmLine = () =>
    decide(async () => {
      await extractionApi.confirm(tenant, target as LineRef);
      return x.confirmed;
    }, x.saveFailed);
  const revert = () =>
    decide(async () => {
      const r = await extractionApi.revert(tenant, target as LineRef);
      return r.reverted ? x.reverted : x.nothingToRevert;
    }, x.saveFailed);

  async function reingest(doc: string) {
    setBusy(true);
    setNotice(null);
    try {
      const r = await extractionApi.reingest(tenant, doc);
      setNotice({ ok: true, text: r.sources.length > 0 ? x.reingestDone(r.sources) : x.reingestNone });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setNotice({ ok: false, text: failure(x.reingestFailed, e) });
    } finally {
      setBusy(false);
      setDialog(null);
    }
  }

  const page = pageLoad.data && target && pageLoad.data.source_doc === target.source_doc && pageLoad.data.page === target.page ? pageLoad.data : null;
  const line: PageLine | undefined = page?.lines.find((l) => l.id === target?.line_id);

  return (
    <div>
      <h1 ref={headingRef} tabIndex={-1}>
        {x.title}
      </h1>
      <p>{x.intro}</p>

      <form className="toolbar" onSubmit={apply}>
        <label>
          {x.tenant}
          <input value={tenantInput} onChange={(e) => setTenantInput(e.target.value)} maxLength={64} size={14} />
        </label>
        <label>
          {x.maxConfidence}
          <input
            value={maxInput}
            onChange={(e) => setMaxInput(e.target.value)}
            inputMode="decimal"
            size={5}
            placeholder="80"
            aria-describedby="ex-max-hint"
            aria-invalid={formProblem ? true : undefined}
          />
        </label>
        <label>
          <span>
            <input type="checkbox" checked={reviewedInput} onChange={(e) => setReviewedInput(e.target.checked)} /> {x.includeReviewed}
          </span>
        </label>
        <button type="submit">{x.load}</button>
      </form>
      <p id="ex-max-hint" className="muted">
        {x.maxConfidenceHint}
      </p>
      {formProblem && (
        <p role="alert" className="error">
          {formProblem}
        </p>
      )}
      {notice && (
        <p role={notice.ok ? "status" : "alert"} className={notice.ok ? "notice" : "error"}>
          {notice.text}
        </p>
      )}

      <section className="panel" aria-labelledby="ex-docs">
        <h2 id="ex-docs">{x.docsTitle}</h2>
        {docs.error ? (
          <ErrorState message={docs.error} onRetry={docs.reload} />
        ) : !docs.data ? (
          <Loading />
        ) : docs.data.length === 0 ? (
          <p className="empty">{x.docsEmpty}</p>
        ) : (
          <div className="ex-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">{x.colDoc}</th>
                  <th scope="col">{x.colPages}</th>
                  <th scope="col">{x.colLines}</th>
                  <th scope="col">{x.colOpen}</th>
                  <th scope="col">{x.colCorrected}</th>
                  <th scope="col">{x.colConfirmed}</th>
                  <th scope="col">{x.colSaved}</th>
                  <th scope="col">{x.colAction}</th>
                </tr>
              </thead>
              <tbody>
                {docs.data.map((d) => (
                  <tr key={d.source_doc}>
                    <th scope="row">{d.source_doc}</th>
                    <td>{number(d.pages)}</td>
                    <td>{number(d.lines)}</td>
                    <td>{number(d.open)}</td>
                    <td>{number(d.corrected)}</td>
                    <td>{number(d.confirmed)}</td>
                    <td>{dateTime(d.saved_at)}</td>
                    <td>
                      <button type="button" aria-label={`${x.showDocLines}: ${d.source_doc}`} onClick={(e) => showDoc(d.source_doc, e)}>
                        {x.showDocLines}
                      </button>{" "}
                      <button type="button" disabled={busy} aria-label={`${x.reingest}: ${d.source_doc}`} onClick={() => setDialog({ kind: "reingest", doc: d.source_doc })}>
                        {x.reingest}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="ex-layout">
        <section aria-labelledby="ex-items">
          <h2 id="ex-items">{x.itemsTitle}</h2>
          {filter.sourceDoc && (
            <p>
              {x.filteredBy(filter.sourceDoc)}{" "}
              <button type="button" onClick={() => setFilter({ ...filter, sourceDoc: "" })}>
                {x.clearFilter}
              </button>
            </p>
          )}
          {items.error ? (
            <ErrorState message={items.error} onRetry={items.reload} />
          ) : !items.data ? (
            <Loading />
          ) : items.data.length === 0 ? (
            <p className="empty">{x.itemsEmpty}</p>
          ) : (
            <>
              <p className="muted" role="status">
                {x.itemsCount(items.data.length)}
              </p>
              {items.data.length >= ITEM_LIMIT && <p className="warn">{x.itemsLimit(ITEM_LIMIT)}</p>}
              <div className="ex-scroll">
                <table ref={tableRef}>
                  <thead>
                    <tr>
                      <th scope="col">{x.colDoc}</th>
                      <th scope="col">{x.colPage}</th>
                      <th scope="col">{x.colConfidence}</th>
                      <th scope="col">{x.colText}</th>
                      <th scope="col">{x.colState}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.data.map((item) => (
                      <ItemRow key={`${item.source_doc}/${item.page}/${item.line_id}`} item={item} current={!!target && target.source_doc === item.source_doc && target.page === item.page && target.line_id === item.line_id} onPick={pick} />
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>

        <section aria-labelledby="ex-page">
          <h2 id="ex-page">{target ? x.pageTitle(target.page) : x.pageHeading}</h2>
          {!target ? (
            <p className="empty">{x.pageHint}</p>
          ) : pageLoad.error ? (
            <ErrorState message={pageLoad.error} onRetry={pageLoad.reload} />
          ) : !page ? (
            <Loading />
          ) : (
            <>
              <p>
                {target.source_doc} <button type="button" onClick={closePage}>{x.closePage}</button>
              </p>
              <PageViewer tenant={tenant} page={page} selectedId={target.line_id} />
              {editing && line && (
                <LineEditor
                  key={line.id}
                  line={line}
                  busy={busy}
                  onSave={save}
                  onConfirm={confirmLine}
                  onRevert={() => setDialog({ kind: "revert" })}
                  onReingest={() => setDialog({ kind: "reingest", doc: target.source_doc })}
                  onClose={closeEditor}
                />
              )}
              <h3>{x.pageLines}</h3>
              <div className="ex-scroll">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">{x.colConfidence}</th>
                      <th scope="col">{x.colText}</th>
                      <th scope="col">{x.colState}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.lines.map((l) => {
                      const weak = l.confidence == null || l.confidence < page.threshold;
                      return (
                        <tr key={l.id} className={l.id === target.line_id ? "ex-current" : undefined}>
                          <td className={weak ? "ex-low" : undefined}>
                            {confidenceText(l.confidence)}
                            {weak && <span className="muted"> ({x.weak})</span>}
                          </td>
                          <td>
                            <button type="button" className="ex-link" title={x.openLine} onClick={(e) => pick({ source_doc: page.source_doc, page: page.page, line_id: l.id }, e)}>
                              {l.text}
                            </button>
                          </td>
                          <td>{x.states[l.state] ?? l.state}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      </div>

      {dialog?.kind === "revert" && (
        <ConfirmDialog title={x.revertTitle} body={x.revertBody} confirmLabel={x.revertOk} busy={busy} onConfirm={revert} onCancel={() => setDialog(null)} />
      )}
      {dialog?.kind === "reingest" && (
        <ConfirmDialog
          title={x.reingestTitle}
          body={x.reingestBody(dialog.doc)}
          confirmLabel={x.reingestOk}
          busy={busy}
          onConfirm={() => reingest(dialog.doc)}
          onCancel={() => setDialog(null)}
        />
      )}
    </div>
  );
}

function ItemRow({ item, current, onPick }: { item: ReviewItem; current: boolean; onPick: (ref: LineRef, e: { currentTarget: HTMLElement }) => void }) {
  return (
    <tr className={current ? "ex-current" : undefined}>
      <th scope="row">{item.source_doc}</th>
      <td>{item.page}</td>
      <td className="ex-low">{confidenceText(item.confidence)}</td>
      <td>
        <button
          type="button"
          className="ex-link"
          data-pick
          title={x.openLine}
          onClick={(e) => onPick({ source_doc: item.source_doc, page: item.page, line_id: item.line_id }, e)}
        >
          {item.text}
        </button>
      </td>
      <td>{x.states[item.state] ?? item.state}</td>
    </tr>
  );
}
