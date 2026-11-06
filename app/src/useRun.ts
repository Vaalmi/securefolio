import { useCallback, useEffect, useRef, useState } from "react";

import { followRun } from "./api";
import type { RunCard } from "./api";

export interface RunView {
  steps: { name: string; detail: string }[];
  text: string;
  following: boolean;
}

const EMPTY: RunView = { steps: [], text: "", following: false };
const RETRY_MS = 1000;

// Follows a run's events, from the start. Reattaching after a dropped connection or a
// reopened chat asks again from zero and replays what's stored.
export function useRun(
  onSettled: (finished: boolean) => void,
  onCard: (card: RunCard) => void,
) {
  const [view, setView] = useState<RunView>(EMPTY);
  const stop = useRef<AbortController | null>(null);
  const settled = useRef(onSettled);
  const carded = useRef(onCard);
  useEffect(() => {
    settled.current = onSettled;
    carded.current = onCard;
  });
  const follow = useCallback((runId: string) => {
    stop.current?.abort();
    const controller = new AbortController();
    stop.current = controller;
    setView({ ...EMPTY, following: true });
    let finished = false;
    followRun(
      runId,
      0,
      (event) => {
        if (event.event === "step") {
          const step = event.data as { name: string; detail: string };
          setView((v) => ({ ...v, steps: [...v.steps, step] }));
        } else if (event.event === "token") {
          const { text } = event.data as { text: string };
          setView((v) => ({ ...v, text: v.text + text }));
        } else if (event.event === "card") {
          finished = true; // paused: the stream ends here, on purpose
          carded.current(event.data as RunCard);
        } else if (event.event === "done") {
          finished = true;
        }
      },
      controller.signal,
    )
      .catch(() => undefined)
      .finally(() => {
        if (controller.signal.aborted) return;
        if (finished) {
          setView(EMPTY);
          settled.current(true);
        } else {
          // Dropped. The run goes on, so wait a moment and ask to follow it again.
          setTimeout(() => {
            if (!controller.signal.aborted) settled.current(false);
          }, RETRY_MS);
        }
      });
  }, []);
  const clear = useCallback(() => {
    stop.current?.abort();
    setView(EMPTY);
  }, []);
  useEffect(
    () => () => {
      stop.current?.abort();
    },
    [],
  );
  return { view, follow, clear };
}
