import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import { goldenApi } from "../../api/golden";
import { REVIEW_REASONS, VERDICTS, recordsApi, type RecordRow, type Verdict } from "../../api/records";
import { ErrorState, Loading } from "../../components/Feedback";
import { jaRecords } from "../../i18n/records";
import { dateTime } from "../../lib/format";
import { useLoad } from "../../lib/useLoad";
import { useActorName } from "./actor";
import { failureText } from "./errors";

const r = jaRecords;

export function modeText(mode: string | null | undefined): string {
  return mode ? (r.mode[mode] ?? mode) : r.common.none;
}

export function RecordDetail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const [notFound, setNotFound] = useState(false);
  const { data, error, loading, reload } = useLoad(
    () =>
      recordsApi.get(id).catch((e) => {
        setNotFound(e instanceof ApiError && e.status === 404);
        throw e;
      }),
    [id],
    r.common.loadFailed,
  );
  return (
    <aside className="panel rec-detail" aria-label={r.detail.title}>
      <div className="toolbar">
        <h2 style={{ margin: 0 }}>{r.detail.title}</h2>
        <button type="button" onClick={onClose}>
          {r.common.close}
        </button>
      </div>
      {loading && !data && <Loading />}
      {error && !data && (notFound ? <p role="alert" className="error">{r.detail.notFound}</p> : <ErrorState message={error} onRetry={reload} />)}
      {data && (
        <Body
          key={id}
          record={data}
          onSaved={() => {
            reload();
            onChanged();
          }}
        />
      )}
    </aside>
  );
}

function Body({ record, onSaved }: { record: RecordRow; onSaved: () => void }) {
  const hasText = typeof record.question === "string";
  return (
    <div>
      <p className="muted">
        {dateTime(record.t)} ・ {record.tenant_id} ・ {modeText(record.answer_mode)}
      </p>
      {hasText ? (
        <>
          <h3>{r.detail.question}</h3>
          <pre>{record.question}</pre>
          <h3>{r.detail.answer}</h3>
          <pre>{record.answer_text || r.common.none}</pre>
          <h3>{r.detail.sources}</h3>
          {record.citations && record.citations.length > 0 ? (
            <ul>
              {record.citations.map((c, i) => (
                <li key={`${c.chunk_id ?? c.source_doc}-${i}`}>
                  {c.source_doc ?? r.common.none}
                  {c.page !== undefined && c.page !== null ? `（${r.detail.page(c.page)}）` : ""}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{r.detail.noSources}</p>
          )}
        </>
      ) : (
        <p role="note" className="warn">
          {record.anonymized ? r.detail.noTextAnonymized : r.detail.noTextLevel}
        </p>
      )}
      {record.guard_reason && (
        <p>
          <strong>{r.detail.guard}:</strong> {record.guard_reason}
        </p>
      )}
      <h3>{r.detail.feedback}</h3>
      {record.feedback ? (
        <dl>
          <dt>{r.list.colFeedback}</dt>
          <dd>{record.feedback.type ? (r.feedbackType[record.feedback.type] ?? record.feedback.type) : r.common.none}</dd>
          {record.feedback.bad_reason && (
            <>
              <dt>{r.detail.feedbackReason}</dt>
              <dd>{record.feedback.bad_reason}</dd>
            </>
          )}
          {record.feedback.comment && (
            <>
              <dt>{r.detail.feedbackComment}</dt>
              <dd>{record.feedback.comment}</dd>
            </>
          )}
        </dl>
      ) : (
        <p className="muted">{r.detail.noFeedback}</p>
      )}
      {record.ip || record.user_agent ? (
        <p className="muted">
          {r.detail.device}: {[record.ip, record.user_agent].filter(Boolean).join(" / ")}
        </p>
      ) : null}
      {hasText && <ReviewForm record={record} onSaved={onSaved} />}
      {hasText && <MakeGolden record={record} />}
    </div>
  );
}

function ReviewForm({ record, onSaved }: { record: RecordRow; onSaved: () => void }) {
  const current = record.review;
  const [actor, setActor] = useActorName();
  const [verdict, setVerdict] = useState<Verdict | "">(current?.verdict ?? "");
  const [reasons, setReasons] = useState<string[]>(current?.reasons ?? []);
  const [note, setNote] = useState(current?.note ?? "");
  const [expected, setExpected] = useState(current?.expected_answer ?? "");
  const [problem, setProblem] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  function toggle(code: string) {
    setReasons((now) => (now.includes(code) ? now.filter((x) => x !== code) : [...now, code]));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setNotice("");
    if (!verdict) return setProblem(r.review.needVerdict);
    if (!actor.trim()) return setProblem(r.review.needReviewer);
    if (verdict !== "correct" && reasons.length === 0 && !note.trim()) return setProblem(r.review.needReason);
    setProblem("");
    setBusy(true);
    try {
      await recordsApi.review(record.id, { verdict, reviewer: actor.trim(), reasons, note: note.trim(), expected_answer: expected.trim() });
      setNotice(r.review.saved);
      onSaved();
    } catch (e) {
      setProblem(failureText(e, r.review.saveFailedBad));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="rec-form" onSubmit={submit} aria-labelledby="review-title">
      <h3 id="review-title">{r.review.title}</h3>
      {current && (
        <p className="muted">
          {r.review.current}: {r.verdict[current.verdict] ?? current.verdict}
          {current.reviewer ? ` ・ ${r.review.by}: ${current.reviewer}` : ""}
          {current.at ? ` ・ ${dateTime(current.at)}` : ""}
        </p>
      )}
      <fieldset>
        <legend>{r.review.verdict}</legend>
        {VERDICTS.map((v) => (
          <label key={v} className="inline">
            <input type="radio" name="verdict" value={v} checked={verdict === v} onChange={() => setVerdict(v)} />
            {r.verdict[v]}
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>{r.review.reasons}</legend>
        {REVIEW_REASONS.map((code) => (
          <label key={code} className="inline">
            <input type="checkbox" checked={reasons.includes(code)} onChange={() => toggle(code)} />
            {r.reason[code]}
          </label>
        ))}
      </fieldset>
      <label className="block">
        {r.review.note}
        <textarea value={note} maxLength={1000} rows={3} onChange={(e) => setNote(e.target.value)} />
      </label>
      <label className="block">
        {r.review.expected}
        <textarea value={expected} maxLength={2000} rows={4} onChange={(e) => setExpected(e.target.value)} aria-describedby="expected-help" />
      </label>
      <p id="expected-help" className="muted">
        {r.review.expectedHelp}
      </p>
      <label className="block">
        {r.review.reviewer}
        <input value={actor} maxLength={64} onChange={(e) => setActor(e.target.value)} aria-describedby="reviewer-help" />
      </label>
      <p id="reviewer-help" className="muted">
        {r.review.reviewerHelp}
      </p>
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      <button type="submit" disabled={busy}>
        {r.review.submit}
      </button>
    </form>
  );
}

function MakeGolden({ record }: { record: RecordRow }) {
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState("");
  const [problem, setProblem] = useState("");

  async function make() {
    setBusy(true);
    setProblem("");
    try {
      const made = await goldenApi.fromRecord(record.tenant_id, record.id);
      setCreated(made.case_id);
    } catch (e) {
      setProblem(failureText(e, r.golden.failed));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="golden-title">
      <h3 id="golden-title">{r.golden.title}</h3>
      <p className="muted">{r.golden.help}</p>
      {!record.review && <p className="muted">{r.golden.needReview}</p>}
      <button type="button" disabled={busy || !record.review} onClick={make}>
        {r.golden.make}
      </button>
      {created && (
        <p role="status" className="notice">
          {r.golden.created(created)} <Link to="/golden">{r.golden.link}</Link>
        </p>
      )}
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
    </section>
  );
}
