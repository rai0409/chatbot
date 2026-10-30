import { useState } from "react";
import { ApiError } from "../../api/client";
import { ACTION_TYPES, STATUS_AFTER, postReviewAction, type ReviewActionBody, type ReviewItem } from "../../api/reviewQueue";
import { dateTime } from "../../lib/format";
import { jaReviewQueue as j } from "../../i18n/reviewQueue";
import { ConfirmDialog } from "./ConfirmDialog";

const CONFIRM_ACTIONS = new Set(["reject_candidate", "mark_ignored"]);

function show(value: string | null | undefined): string {
  return value ? value : j.noValue;
}

function failureText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return j.forbiddenAction;
    if (e.status === 409) return j.conflict;
    if (e.status === 400) return `${j.badRequest} (${e.detail})`;
  }
  return j.failed;
}

export function ReviewCard({ item }: { item: ReviewItem }) {
  const [actionType, setActionType] = useState<string>(ACTION_TYPES[0]);
  const [statusAfter, setStatusAfter] = useState("");
  const [tags, setTags] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const id = `${item.tenant_id ?? "default"}-${item.review_id ?? ""}`;

  async function submit() {
    setConfirming(false);
    const body: ReviewActionBody = {
      review_id: item.review_id ?? "",
      action_type: actionType,
      feedback_token: null,
      tenant_id: item.tenant_id || "default",
      selected_candidate_id: item.selected_candidate_id || null,
      operator_note: note.trim() || null,
      status_after: statusAfter || null,
      tags: tags
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
    };
    setBusy(true);
    setResult(null);
    try {
      const response = await postReviewAction(body);
      setResult(
        response.stored
          ? { ok: true, text: j.stored(response.action_id) }
          : { ok: false, text: j.notStored(response.warning || "not stored") },
      );
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setResult({ ok: false, text: failureText(e) });
    } finally {
      setBusy(false);
    }
  }

  const rows: Array<[string, string]> = [
    [j.fieldAnswerMode, show(item.answer_mode)],
    [j.fieldConfidenceRoute, show(item.confidence_route)],
    [j.fieldDecisionRoute, show(item.decision_route)],
    [j.fieldCandidateIds, show(item.candidate_ids.join(", "))],
    [j.fieldSelected, show(item.selected_candidate_id)],
    [j.fieldFeedbackType, show(item.feedback_type)],
    [j.fieldBadReason, show(item.bad_reason)],
    [j.fieldSource, show(item.source)],
    [j.fieldCreatedAt, dateTime(item.created_at)],
    [j.fieldReasons, show(item.reasons.join(", "))],
  ];

  return (
    <li className="rq-card" aria-label={item.review_id ?? undefined}>
      <div className="rq-head">
        <div className="rq-badges">
          <span className={`badge rq-priority-${item.priority ?? ""}`}>{j.priorities[item.priority ?? ""] ?? show(item.priority)}</span>
          <span className="badge">{show(item.status)}</span>
          <span className="badge">{show(item.tenant_id)}</span>
        </div>
        <span className="badge">{show(item.review_id)}</span>
      </div>
      <p className="rq-query">{item.user_query}</p>
      <dl className="rq-grid">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <form
        className="rq-action"
        onSubmit={(e) => {
          e.preventDefault();
          if (CONFIRM_ACTIONS.has(actionType)) setConfirming(true);
          else void submit();
        }}
      >
        <label htmlFor={`a-${id}`}>
          {j.actionLabel}
          <select id={`a-${id}`} value={actionType} onChange={(e) => setActionType(e.target.value)}>
            {ACTION_TYPES.map((a) => (
              <option key={a} value={a}>
                {j.actions[a]}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`s-${id}`}>
          {j.statusAfter}
          <select id={`s-${id}`} value={statusAfter} onChange={(e) => setStatusAfter(e.target.value)}>
            <option value="">{j.statusAfterNone}</option>
            {STATUS_AFTER.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`t-${id}`}>
          {j.tags}
          <input id={`t-${id}`} value={tags} placeholder={j.tagsPlaceholder} onChange={(e) => setTags(e.target.value)} />
        </label>
        <label htmlFor={`n-${id}`} className="rq-wide">
          {j.note}
          <input id={`n-${id}`} value={note} placeholder={j.notePlaceholder} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
        </label>
        <div>
          <button type="submit" disabled={busy}>
            {busy ? j.saving : j.submit}
          </button>
        </div>
      </form>
      {result && (
        <p role={result.ok ? "status" : "alert"} className={result.ok ? "notice" : "error"}>
          {result.text}
        </p>
      )}
      {confirming && (
        <ConfirmDialog
          title={j.confirmTitle}
          body={j.confirmBody}
          confirmLabel={j.confirm}
          cancelLabel={j.cancel}
          busy={busy}
          onConfirm={() => void submit()}
          onCancel={() => setConfirming(false)}
        />
      )}
    </li>
  );
}
