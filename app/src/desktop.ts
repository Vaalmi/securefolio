import { invoke, isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

// Everything native goes through here: in a browser (the tests, `npm run dev`)
// each call has a stand-in.

const DEV_CONNECTION = "/__connection";

// The backend's address and token, as the shell hands them to the app.
export interface Connection {
  base: string;
  token: string;
}

export async function connectionInfo(): Promise<Connection> {
  if (isTauri()) return invoke<Connection>("connection");
  const response = await fetch(DEV_CONNECTION);
  return (await response.json()) as Connection;
}

// The browser stand-in reads what a test has set up to be "chosen".
declare global {
  interface Window {
    securefolioChosenPaths?: string[];
  }
}

function chosen(): string[] {
  return window.securefolioChosenPaths ?? [];
}

export async function chooseFolder(): Promise<string | null> {
  if (!isTauri()) return chosen()[0] ?? null;
  const picked = await open({ directory: true, multiple: false });
  return typeof picked === "string" ? picked : null;
}

export async function chooseFiles(): Promise<string[]> {
  if (!isTauri()) return chosen();
  const picked = await open({ directory: false, multiple: true });
  return picked ?? [];
}

export async function chooseFile(): Promise<string | null> {
  if (!isTauri()) return chosen()[0] ?? null;
  const picked = await open({ directory: false, multiple: false });
  return typeof picked === "string" ? picked : null;
}

export async function showInFinder(path: string): Promise<void> {
  if (isTauri()) await invoke("show_in_finder", { path });
}

export async function openAtLogin(enabled: boolean): Promise<void> {
  if (isTauri()) await invoke("set_open_at_login", { enabled });
}

export async function isOpenAtLogin(): Promise<boolean> {
  if (!isTauri()) return false;
  return invoke<boolean>("is_open_at_login");
}

export async function installExtension(): Promise<void> {
  if (isTauri()) await invoke("install_extension");
}

export async function bridgePath(): Promise<string> {
  if (!isTauri()) return "securefolio-mcp";
  return invoke<string>("bridge_path");
}

export async function restartBackend(): Promise<void> {
  if (!isTauri()) return;
  await invoke("restart_backend");
}

export async function hideQuickSearch(): Promise<void> {
  if (isTauri()) await invoke("hide_quick");
}

// From the quick-search window, a place opens in the main window. In a browser there
// is only the one window, so it opens there and this says so.
export async function openInMainWindow(route: string): Promise<boolean> {
  if (!isTauri()) return false;
  await invoke("open_main", { route });
  return true;
}

// Paths dropped on the window, from Tauri's own drag-and-drop event; a test sends
// the same event in a browser.
export const DROP_EVENT = "securefolio-drop";

export async function onPathsDropped(
  handler: (paths: string[]) => void,
): Promise<() => void> {
  const listener = (event: Event) => {
    handler((event as CustomEvent<string[]>).detail);
  };
  window.addEventListener(DROP_EVENT, listener);
  let unlisten: (() => void) | undefined;
  if (isTauri()) {
    const { getCurrentWebview } = await import("@tauri-apps/api/webview");
    unlisten = await getCurrentWebview().onDragDropEvent((event) => {
      if (event.payload.type === "drop") handler(event.payload.paths);
    });
  }
  return () => {
    window.removeEventListener(DROP_EVENT, listener);
    unlisten?.();
  };
}

// A deep link from Claude, delivered by the shell as a route.
export async function onOpenRoute(
  handler: (route: string) => void,
): Promise<() => void> {
  if (!isTauri()) return () => undefined;
  const { listen } = await import("@tauri-apps/api/event");
  return listen<string>("open-route", (event) => {
    handler(event.payload);
  });
}
