import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { type IngestionJob, ingestionApi } from "../../api/ingestion";
import { ji } from "../../i18n/ingestion";
import { type Outcome, ResultView, failureMessage } from "./parts";

const WATCHING = new Set(["queued", "running"]);
const MAX_TRIES = 1800; // about an hour at two seconds

interface Props {
  jobId: string;
  /** What is known before the first answer (the response that started the job). */
  initial?: Outcome | null;
  /** Called when the job has left queued/running (the job list is refreshed). */
  onSettled: () => void;
  onClose?: () => void;
}

/** One job with its status. A job that is queued or running is read again every two seconds until it is over. */
export function JobView({ jobId, initial = null, onSettled, onClose }: Props) {
  const [outcome, setOutcome] = useState<Outcome | null>(initial);
  const [tick, setTick] = useState(0);
  const settled = useRef(onSettled);
  settled.current = onSettled;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let tries = 0;
    const poll = async () => {
      let next = 5000;
      try {
        const job: IngestionJob = await ingestionApi.job(jobId);
        if (cancelled) return;
        setOutcome({ data: job });
        if (!WATCHING.has(job.status)) {
          settled.current();
          return;
        }
        next = 2000;
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
          setOutcome({ error: e.status === 404 ? ji.jobs.notFound : failureMessage(e, ji.common.failed) });
          return;
        }
      }
      if (++tries <= MAX_TRIES) timer = setTimeout(poll, next);
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, tick]);

  const running = WATCHING.has(String(outcome?.data?.status));
  return (
    <div className="panel">
      <h3>{ji.jobs.detailHeading}</h3>
      <p className="muted">
        {ji.jobs.colJob}: {jobId}
      </p>
      {running && <p role="status" className="muted">{ji.jobs.watching}</p>}
      <ResultView outcome={outcome} label={ji.jobs.detailHeading} />
      <div className="toolbar">
        <button type="button" onClick={() => setTick((n) => n + 1)}>
          {ji.jobs.refresh}
        </button>
        {onClose && (
          <button type="button" onClick={onClose}>
            {ji.jobs.close}
          </button>
        )}
      </div>
    </div>
  );
}
