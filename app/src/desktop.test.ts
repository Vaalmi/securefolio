import { afterEach, describe, expect, it, vi } from "vitest";

import {
  bridgePath,
  chooseFile,
  chooseFiles,
  chooseFolder,
  DROP_EVENT,
  installExtension,
  isOpenAtLogin,
  onOpenRoute,
  onPathsDropped,
  openAtLogin,
  restartBackend,
  showInFinder,
} from "./desktop";

afterEach(() => {
  delete window.securefolioChosenPaths;
});

describe("the browser stand-ins", () => {
  it("choose what a test set up", async () => {
    window.securefolioChosenPaths = ["/a", "/b"];
    await expect(chooseFolder()).resolves.toBe("/a");
    await expect(chooseFile()).resolves.toBe("/a");
    await expect(chooseFiles()).resolves.toEqual(["/a", "/b"]);
  });

  it("choose nothing by default", async () => {
    await expect(chooseFolder()).resolves.toBeNull();
    await expect(chooseFile()).resolves.toBeNull();
    await expect(chooseFiles()).resolves.toEqual([]);
  });

  it("do nothing for native-only calls", async () => {
    await expect(showInFinder("/a")).resolves.toBeUndefined();
    await expect(openAtLogin(true)).resolves.toBeUndefined();
    await expect(isOpenAtLogin()).resolves.toBe(false);
    await expect(installExtension()).resolves.toBeUndefined();
    await expect(restartBackend()).resolves.toBeUndefined();
    await expect(bridgePath()).resolves.toBe("securefolio-mcp");
    const stop = await onOpenRoute(() => undefined);
    stop();
  });

  it("hear dropped paths from the test's event, until stopped", async () => {
    const seen = vi.fn();
    const stop = await onPathsDropped(seen);
    window.dispatchEvent(new CustomEvent(DROP_EVENT, { detail: ["/x"] }));
    expect(seen).toHaveBeenCalledWith(["/x"]);
    stop();
    window.dispatchEvent(new CustomEvent(DROP_EVENT, { detail: ["/y"] }));
    expect(seen).toHaveBeenCalledTimes(1);
  });
});
