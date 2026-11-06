import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  isTauri: () => true,
  invoke: vi.fn((command: string) =>
    Promise.resolve(
      command === "connection" ? { base: "b", token: "t" } : command,
    ),
  ),
}));
vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(),
}));
const stopListening = vi.fn();
vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => ({
    onDragDropEvent: (handler: (event: unknown) => void) => {
      handler({ payload: { type: "over" } });
      handler({ payload: { type: "drop", paths: ["/x"] } });
      return Promise.resolve(stopListening);
    },
  }),
}));
vi.mock("@tauri-apps/api/event", () => ({
  listen: (_name: string, handler: (event: unknown) => void) => {
    handler({ payload: "/doc/d1" });
    return Promise.resolve(stopListening);
  },
}));

// The test setup has already loaded the module with the real isTauri.
vi.resetModules();
const { open } = await import("@tauri-apps/plugin-dialog");
const {
  bridgePath,
  chooseFile,
  chooseFiles,
  chooseFolder,
  connectionInfo,
  installExtension,
  isOpenAtLogin,
  onOpenRoute,
  onPathsDropped,
  openAtLogin,
  restartBackend,
  showInFinder,
} = await import("./desktop");

afterEach(() => {
  vi.mocked(open).mockReset();
});

describe("in the desktop shell", () => {
  it("gets its connection and calls its commands", async () => {
    await expect(connectionInfo()).resolves.toEqual({ base: "b", token: "t" });
    await showInFinder("/a");
    await openAtLogin(false);
    await installExtension();
    await restartBackend();
    await expect(isOpenAtLogin()).resolves.toBe("is_open_at_login");
    await expect(bridgePath()).resolves.toBe("bridge_path");
  });

  it("uses the native pickers", async () => {
    vi.mocked(open).mockResolvedValue("/f");
    await expect(chooseFolder()).resolves.toBe("/f");
    await expect(chooseFile()).resolves.toBe("/f");
    vi.mocked(open).mockResolvedValue(null);
    await expect(chooseFolder()).resolves.toBeNull();
    await expect(chooseFile()).resolves.toBeNull();
    await expect(chooseFiles()).resolves.toEqual([]);
    vi.mocked(open).mockResolvedValue(["/g"] as never);
    await expect(chooseFiles()).resolves.toEqual(["/g"]);
  });

  it("hears drops and deep links from Tauri", async () => {
    const dropped = vi.fn();
    const stop = await onPathsDropped(dropped);
    expect(dropped).toHaveBeenCalledWith(["/x"]);
    stop();
    expect(stopListening).toHaveBeenCalled();
    const route = vi.fn();
    await onOpenRoute(route);
    expect(route).toHaveBeenCalledWith("/doc/d1");
  });
});
