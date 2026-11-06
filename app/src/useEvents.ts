import { useEffect, useState } from "react";

import { followJobs, getJobCounts } from "./api";
import type { TrayCounts } from "./api";

const RECONNECT_MS = 2000;

// The queue's counts, kept current over SSE. A dropped stream reconnects.
export function useJobCounts(): TrayCounts | undefined {
  const [counts, setCounts] = useState<TrayCounts>();
  useEffect(() => {
    const stop = new AbortController();
    const follow = async (): Promise<void> => {
      while (!stop.signal.aborted) {
        try {
          setCounts(await getJobCounts());
          await followJobs((event) => {
            setCounts(event.data as TrayCounts);
          }, stop.signal);
        } catch {
          // The backend is restarting; try again shortly.
        }
        await new Promise((done) => setTimeout(done, RECONNECT_MS));
      }
    };
    void follow();
    return () => {
      stop.abort();
    };
  }, []);
  return counts;
}
