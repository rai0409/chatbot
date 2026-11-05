import { useState } from "react";
import type { QaCitation, QaHistoryRow, QaRecord, QaSave } from "../../api/approvedQa";
import { jaApprovedQa as q } from "../../i18n/approvedQa";
import { rememberActor, storedActor } from "../../lib/actor";
import { dateTime } from "../../lib/format";

interface Props {
  tenantDefault: string;
  record: QaRecord | null;
  history: QaHistoryRow[];
  busy: boolean;
  problem: string;
  onSave: (body: QaSave) => void;
  onClose: () => void;
}

interface CitationInput {
  source_doc: string;
  pages: string;
  title: string;
}

function toInputs(record: QaRecord | null): CitationInput[] {
  const found = (record?.approved_citations ?? []).map((c) => ({ source_doc: c.source_doc, pages: (c.source_pages ?? []).join(","), title: c.title ?? "" }));
  return found.length ? found : [{ source_doc: "", pages: "", title: "" }];
}

/** Create or edit one QA. Saving needs a reason and a name; the server decides whether the edit takes an approved record back to draft. */
export function RecordForm({ tenantDefault, record, history, busy, problem, onSave, onClose }: Props) {
  const [tenant, setTenant] = useState(record?.tenant_id ?? tenantDefault);
  const [question, setQuestion] = useState(record?.question ?? "");
  const [answer, setAnswer] = useState(record?.approved_answer ?? "");
  const [aliases, setAliases] = useState((record?.approved_aliases?.length ? record.approved_aliases : record?.proposed_aliases ?? []).join("\n"));
  const [tags, setTags] = useState((record?.tags ?? []).join(", "));
  const [version, setVersion] = useState(record?.doc_version ?? "");
  const [citations, setCitations] = useState<CitationInput[]>(toInputs(record));
  const [reason, setReason] = useState("");
  const [actor, setActor] = useState(storedActor);
  const [local, setLocal] = useState("");

  function patchCitation(index: number, change: Partial<CitationInput>) {
    setCitations((list) => list.map((c, i) => (i === index ? { ...c, ...change } : c)));
  }

  function submit() {
    if (!question.trim() || !answer.trim()) return setLocal(q.needQuestion);
    if (!reason.trim() || !actor.trim()) return setLocal(q.needReason);
    const parsed: QaCitation[] = [];
    for (const c of citations) {
      if (!c.source_doc.trim() && !c.pages.trim() && !c.title.trim()) continue;
      const pages = c.pages.split(/[,、\s]+/).filter(Boolean).map(Number);
      if (pages.some((p) => !Number.isInteger(p) || p < 1)) return setLocal(q.invalidPages);
      parsed.push({ source_doc: c.source_doc.trim(), source_pages: pages, ...(c.title.trim() ? { title: c.title.trim() } : {}) });
    }
    setLocal("");
    rememberActor(actor.trim());
    onSave({
      tenant_id: tenant.trim() || "default",
      ...(record ? { qa_id: record.qa_id, expected_updated_at: record.updated_at } : {}),
      question: question.trim(),
      approved_answer: answer.trim(),
      aliases: aliases.split("\n").map((s) => s.trim()).filter(Boolean),
      tags: tags.split(/[,、]/).map((s) => s.trim()).filter(Boolean),
      citations: parsed,
      doc_version: version.trim() || null,
      reason: reason.trim(),
      actor: actor.trim(),
    });
  }

  return (
    <section className="panel" aria-labelledby="qa-form-title">
      <h2 id="qa-form-title">{record ? q.formEdit : q.formNew}</h2>
      <div className="toolbar">
        <label>
          {q.tenant}
          <input value={tenant} onChange={(e) => setTenant(e.target.value)} maxLength={64} disabled={!!record} />
        </label>
        <label>
          {q.docVersion}
          <input value={version} onChange={(e) => setVersion(e.target.value)} maxLength={100} />
        </label>
      </div>
      {/* The label is not wrapped around the text box: a label that contains a filled text box takes its content into the
          accessible name ("回答毎月末日です。"), which a screen reader would read out as the name of the field. */}
      <div>
        <label htmlFor="qa-question">{q.question}</label>
        <textarea id="qa-question" value={question} onChange={(e) => setQuestion(e.target.value)} rows={2} maxLength={550} style={{ width: "100%" }} />
      </div>
      <div>
        <label htmlFor="qa-answer">{q.answer}</label>
        <textarea id="qa-answer" value={answer} onChange={(e) => setAnswer(e.target.value)} rows={5} maxLength={5500} style={{ width: "100%" }} />
      </div>
      <div>
        <label htmlFor="qa-aliases">{q.aliases}</label>
        <textarea id="qa-aliases" value={aliases} onChange={(e) => setAliases(e.target.value)} rows={3} style={{ width: "100%" }} aria-describedby="qa-alias-help" />
      </div>
      <p id="qa-alias-help" className="muted">
        {q.aliasesHelp}
      </p>
      <label>
        {q.tags}
        <input value={tags} onChange={(e) => setTags(e.target.value)} size={40} />
      </label>
      <fieldset>
        <legend>{q.citations}</legend>
        <p className="muted">{q.citationsHelp}</p>
        {citations.map((c, i) => (
          <div className="toolbar" key={i}>
            <label>
              {q.sourceDoc}
              <input value={c.source_doc} onChange={(e) => patchCitation(i, { source_doc: e.target.value })} maxLength={300} />
            </label>
            <label>
              {q.sourcePages}
              <input value={c.pages} onChange={(e) => patchCitation(i, { pages: e.target.value })} size={10} />
            </label>
            <label>
              {q.sourceTitle}
              <input value={c.title} onChange={(e) => patchCitation(i, { title: e.target.value })} maxLength={300} />
            </label>
            <button type="button" aria-label={`${q.removeCitation} ${i + 1}`} onClick={() => setCitations((list) => (list.length > 1 ? list.filter((_, j) => j !== i) : [{ source_doc: "", pages: "", title: "" }]))}>
              ×
            </button>
          </div>
        ))}
        <button type="button" onClick={() => setCitations((list) => [...list, { source_doc: "", pages: "", title: "" }])}>
          {q.addCitation}
        </button>
      </fieldset>
      <div className="toolbar">
        <label>
          {q.reason}
          <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} size={40} />
        </label>
        <label>
          {q.actor}
          <input value={actor} onChange={(e) => setActor(e.target.value)} maxLength={64} />
        </label>
      </div>
      {(local || problem) && (
        <p role="alert" className="error">
          {local || problem}
        </p>
      )}
      <div className="toolbar">
        <button type="button" disabled={busy} onClick={submit}>
          {q.save}
        </button>
        <button type="button" disabled={busy} onClick={onClose}>
          {q.close}
        </button>
      </div>
      {record && (
        <>
          <h3>{q.history}</h3>
          {history.length === 0 ? (
            <p className="empty">{q.historyEmpty}</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th scope="col">{q.colWhen}</th>
                  <th scope="col">{q.colBy}</th>
                  <th scope="col">{q.colWhat}</th>
                  <th scope="col">{q.reason}</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h, i) => (
                  <tr key={`${h.at}-${i}`}>
                    <td>{dateTime(h.at)}</td>
                    <td>{h.by}</td>
                    <td>
                      {q.actions[h.action] ?? h.action}
                      {h.status_before !== h.status_after && h.status_after ? `（${q.statuses[h.status_before ?? ""] ?? "—"} → ${q.statuses[h.status_after] ?? h.status_after}）` : ""}
                    </td>
                    <td>{h.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}
