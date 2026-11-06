import { useEffect } from "react";

const THREADS_CHANGED = "securefolio:threads-changed";

/** Runs `reload` when a chat is renamed, pinned or deleted anywhere in the app. */
export function useThreadsChanged(reload: () => void): void {
  useEffect(() => {
    window.addEventListener(THREADS_CHANGED, reload);
    return () => {
      window.removeEventListener(THREADS_CHANGED, reload);
    };
  }, [reload]);
}

export function threadsChanged(): void {
  window.dispatchEvent(new Event(THREADS_CHANGED));
}
