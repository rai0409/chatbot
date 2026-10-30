import { useState } from "react";
import { ApiError } from "../../api/client";
import { goldenApi, type CaseInput, type GoldenCase } from "../../api/golden";
import { jaGolden } from "../../i18n/golden";

const f = jaGolden.form;

const lines = (text: string) => text.split("\n").map((s) => s.trim()).filter(Boolean);
const commas = (text: string) => text.split(/[,、]/).map((s) => s.trim()).filter(Boolean);

interface Props {
  tenant: string;
  /** The case being edited; none when creating. */
  editing: GoldenCase | null;
  onDone: (message: string) => void;
  onCancel: () => void;
  onReload: () => void;
}

export function CaseForm({ tenant, editing, onDone, onCancel, onReload }: Props) {
  const [caseId, setCaseId] = useState("");
  const [question, setQuestion] = useState(editing?.question ?? "");
  const [all, setAll] = useState((editing?.expected_all ?? []).join("\n"));
  const [any, setAny] = useState((editing?.expected_any ?? []).join("\n"));
  const [forbidden, setForbidden] = useState((editing?.forbidden_any ?? []).join("\n"));
  const [reference, setReference] = useState(editing?.reference ?? "");
  const [qtype, setQtype] = useState(editing?.expected_question_type ?? "");
  const [group, setGroup] = useState(editing?.group ?? "");
  const [tags, setTags] = useState((editing?.tags ?? []).join(", "));
  const [citation, setCitation] = useState(editing?.expected_citation ?? false);
  const [enabled, setEnabled] = useState(editing?.enabled ?? true);
  const [priority, setPriority] = useState(String(editing?.priority ?? 0));
  const [errors, setErrors] = useState<string[]>([]);
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const found: string[] = [];
    if (!question.trim()) found.push(f.needQuestion);
    if (!/^-?\d+$/.test(priority.trim())) found.push(f.badPriority);
    const body: CaseInput = {
      question: question.trim(),
      expected_all: lines(all),
      expected_any: lines(any),
      forbidden_any: lines(forbidden),
      reference: reference.trim(),
      expected_question_type: qtype.trim(),
      group: group.trim(),
      tags: commas(tags),
      expected_citation: citation,
      enabled,
      priority: Number(priority.trim()),
    };
    const checks = body.expected_all.length || body.expected_any.length || body.forbidden_any.length || body.reference || body.expected_question_type || citation;
    if (question.trim() && !checks) found.push(f.needCheck);
    setErrors(found);
    if (found.length) return;
    setBusy(true);
    setConflict(false);
    try {
      if (editing) {
        await goldenApi.update(tenant, editing.case_id, { ...body, expected_updated_at: editing.updated_at });
        onDone(jaGolden.changed);
      } else {
        const made = await goldenApi.create(tenant, { ...body, ...(caseId.trim() ? { case_id: caseId.trim() } : {}) });
        onDone(f.created(made.case_id));
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && editing) setConflict(true);
      else if (e instanceof ApiError && e.status === 409) setErrors([e.detail]);
      else if (e instanceof ApiError && e.status === 403) setErrors([jaGolden.notAllowed]);
      else if (e instanceof ApiError && e.status === 404) setErrors([jaGolden.notFound]);
      else if (e instanceof ApiError && e.status === 400) setErrors(e.detail.split("; "));
      else setErrors([f.saveFailed]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel golden-form" onSubmit={submit} aria-labelledby="golden-form-title" noValidate>
      <h2 id="golden-form-title" style={{ marginTop: 0 }}>
        {editing ? f.editTitle(editing.case_id) : f.createTitle}
      </h2>
      <p className="warn">{f.mustCheck}</p>
      {!editing && (
        <Field label={f.caseId} help={f.caseIdHelp} id="g-id">
          <input id="g-id" value={caseId} maxLength={64} onChange={(e) => setCaseId(e.target.value)} aria-describedby="g-id-help" />
        </Field>
      )}
      <Field label={f.question} help={f.questionHelp} id="g-question">
        <textarea id="g-question" value={question} rows={2} maxLength={1000} onChange={(e) => setQuestion(e.target.value)} aria-describedby="g-question-help" />
      </Field>
      <Field label={f.expectedAll} help={f.expectedAllHelp} id="g-all">
        <textarea id="g-all" value={all} rows={3} onChange={(e) => setAll(e.target.value)} aria-describedby="g-all-help" />
      </Field>
      <Field label={f.expectedAny} help={f.expectedAnyHelp} id="g-any">
        <textarea id="g-any" value={any} rows={3} onChange={(e) => setAny(e.target.value)} aria-describedby="g-any-help" />
      </Field>
      <Field label={f.forbiddenAny} help={f.forbiddenAnyHelp} id="g-forbidden">
        <textarea id="g-forbidden" value={forbidden} rows={3} onChange={(e) => setForbidden(e.target.value)} aria-describedby="g-forbidden-help" />
      </Field>
      <Field label={f.reference} help={f.referenceHelp} id="g-reference">
        <textarea id="g-reference" value={reference} rows={3} maxLength={4000} onChange={(e) => setReference(e.target.value)} aria-describedby="g-reference-help" />
      </Field>
      <Field label={f.questionType} help={f.questionTypeHelp} id="g-qtype">
        <input id="g-qtype" value={qtype} maxLength={40} onChange={(e) => setQtype(e.target.value)} aria-describedby="g-qtype-help" />
      </Field>
      <Field label={f.group} help={f.groupHelp} id="g-group">
        <input id="g-group" value={group} maxLength={80} onChange={(e) => setGroup(e.target.value)} aria-describedby="g-group-help" />
      </Field>
      <Field label={f.tags} help={f.tagsHelp} id="g-tags">
        <input id="g-tags" value={tags} onChange={(e) => setTags(e.target.value)} aria-describedby="g-tags-help" />
      </Field>
      <div className="golden-field">
        <label>
          <input type="checkbox" checked={citation} onChange={(e) => setCitation(e.target.checked)} aria-describedby="g-citation-help" /> {f.citation}
        </label>
        <p id="g-citation-help" className="muted">
          {f.citationHelp}
        </p>
      </div>
      <div className="golden-field">
        <label>
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} aria-describedby="g-enabled-help" /> {f.enabled}
        </label>
        <p id="g-enabled-help" className="muted">
          {f.enabledHelp}
        </p>
      </div>
      <Field label={f.priority} help={f.priorityHelp} id="g-priority">
        <input id="g-priority" inputMode="numeric" value={priority} onChange={(e) => setPriority(e.target.value)} aria-describedby="g-priority-help" />
      </Field>

      {errors.length > 0 && (
        <div role="alert" className="error" aria-label={f.errors}>
          <ul>
            {errors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </div>
      )}
      {conflict && (
        <div role="alert" className="error">
          <p>{jaGolden.conflict}</p>
          <button type="button" onClick={onReload}>
            {jaGolden.reload}
          </button>
        </div>
      )}
      <button type="submit" disabled={busy}>
        {busy ? f.saving : f.save}
      </button>{" "}
      <button type="button" onClick={onCancel}>
        {f.cancel}
      </button>
    </form>
  );
}

function Field({ label, help, id, children }: { label: string; help: string; id: string; children: React.ReactNode }) {
  return (
    <div className="golden-field">
      <label htmlFor={id}>{label}</label>
      {children}
      <p id={`${id}-help`} className="muted">
        {help}
      </p>
    </div>
  );
}
