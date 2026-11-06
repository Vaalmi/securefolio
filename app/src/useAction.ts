import { useState } from "react";

// Runs an action against the backend, saying while it runs and what went wrong.

export interface Action<A extends unknown[]> {
  run: (...args: A) => Promise<void>;
  busy: boolean;
  error: string | undefined;
}

export function useAction<A extends unknown[]>(
  act: (...args: A) => Promise<unknown>,
  then?: () => void,
): Action<A> {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function run(...args: A) {
    setBusy(true);
    setError(undefined);
    try {
      await act(...args);
      then?.();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }

  return { run, busy, error };
}
