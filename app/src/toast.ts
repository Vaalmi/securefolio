// A one-at-a-time message, shown by ToastHost.

const SHOWN_MS = 5000;

export interface Toast {
  text: string;
  undo: (() => void) | undefined;
}

let current: Toast | null = null;
const listeners = new Set<() => void>();
let timer: number | undefined;

function changed(): void {
  listeners.forEach((listener) => {
    listener();
  });
}

export function toast(text: string, undo?: () => void): void {
  current = { text, undo };
  changed();
  window.clearTimeout(timer);
  timer = window.setTimeout(() => {
    current = null;
    changed();
  }, SHOWN_MS);
}

export function dismissToast(): void {
  current = null;
  changed();
}

export function subscribeToToast(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function currentToast(): Toast | null {
  return current;
}
