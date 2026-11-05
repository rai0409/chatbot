import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

export interface Loaded<T> {
  data: T | null;
  error: string;
  loading: boolean;
  reload: () => void;
}

/** Load something when the screen opens (and again when `deps` change). A 401 goes to the login page in the client, so it is not an error here. */
export function useLoad<T>(load: () => Promise<T>, deps: ReadonlyArray<unknown>, failureText: string): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, deps);

  const reload = useCallback(() => {
    const mine = ++seq.current;
    setLoading(true);
    setError("");
    run()
      .then((value) => mine === seq.current && setData(value))
      .catch((e) => {
        if (mine === seq.current && !(e instanceof ApiError && e.status === 401)) setError(failureText);
      })
      .finally(() => mine === seq.current && setLoading(false));
  }, [run, failureText]);

  useEffect(() => {
    reload();
    return () => {
      seq.current++;
    };
  }, [reload]);

  return { data, error, loading, reload };
}
