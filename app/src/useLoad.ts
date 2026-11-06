import { useCallback, useEffect, useRef, useState } from "react";

// Loads something from the backend when `key` changes, and again every
// `every` milliseconds while `again` says so.

export interface Loaded<T> {
  data: T | undefined;
  error: string | undefined;
  reload: () => void;
}

export interface Again<T> {
  every: number | ((data: T) => number); // a fixed wait, or one the data decides
  while: (data: T) => boolean;
}

export function useLoad<T>(
  load: () => Promise<T>,
  key: string,
  again?: Again<T>,
): Loaded<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const [round, setRound] = useState(0);
  const latest = useRef(load);
  const reload = useCallback(() => {
    setRound((r) => r + 1);
  }, []);

  useEffect(() => {
    latest.current = load;
  });

  useEffect(() => {
    let current = true;
    latest
      .current()
      .then((loaded) => {
        if (!current) return;
        setData(loaded);
        setError(undefined);
      })
      .catch((failure: unknown) => {
        if (current) {
          setError(
            failure instanceof Error ? failure.message : String(failure),
          );
        }
      });
    return () => {
      current = false;
    };
  }, [key, round]);

  const wait = again?.every;
  let every: number | undefined;
  if (typeof wait === "number") every = wait;
  else if (wait !== undefined && data !== undefined) every = wait(data);
  const keepGoing = data !== undefined && again ? again.while(data) : false;
  useEffect(() => {
    if (!keepGoing || every === undefined) return;
    const timer = setTimeout(reload, every);
    return () => {
      clearTimeout(timer);
    };
  }, [keepGoing, every, reload, data]);

  return { data, error, reload };
}
