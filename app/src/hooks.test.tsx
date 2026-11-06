import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { del, get, patch, put } from "./connection";
import { useAction } from "./useAction";
import { useLoad } from "./useLoad";

describe("useLoad", () => {
  it("loads, reloads, and reports a failure", async () => {
    const load = vi
      .fn<() => Promise<number>>()
      .mockResolvedValueOnce(1)
      .mockRejectedValueOnce(new Error("Gone."));
    const { result } = renderHook(() => useLoad(load, "k"));
    await waitFor(() => {
      expect(result.current.data).toBe(1);
    });
    act(() => {
      result.current.reload();
    });
    await waitFor(() => {
      expect(result.current.error).toBe("Gone.");
    });
  });

  it("reports a failure that isn't an Error, and loads again while asked", async () => {
    const load = vi
      .fn<() => Promise<number>>()
      .mockRejectedValueOnce("plain")
      .mockResolvedValue(2);
    const { result } = renderHook(() =>
      useLoad(load, "k", { every: 1, while: (n) => n < 2 }),
    );
    await waitFor(() => {
      expect(result.current.error).toBe("plain");
    });
    act(() => {
      result.current.reload();
    });
    await waitFor(() => {
      expect(result.current.data).toBe(2);
    });
  });

  it("loads again on a timer while asked", async () => {
    let n = 0;
    const load = () => {
      n += 1;
      return Promise.resolve(n);
    };
    const { result } = renderHook(() =>
      useLoad(load, "k", { every: 1, while: (value) => value < 3 }),
    );
    await waitFor(() => {
      expect(result.current.data).toBe(3);
    });
  });
});

describe("useAction", () => {
  it("runs, then calls what's next", async () => {
    const then = vi.fn();
    const { result } = renderHook(() =>
      useAction(() => Promise.resolve(1), then),
    );
    await act(() => result.current.run());
    expect(then).toHaveBeenCalled();
    expect(result.current.busy).toBe(false);
  });

  it("keeps the reason when it fails", async () => {
    const { result } = renderHook(() =>
      useAction(() => Promise.reject(new Error("No."))),
    );
    await act(() => result.current.run());
    expect(result.current.error).toBe("No.");
  });

  it("keeps a reason that isn't an Error", async () => {
    const reject = (): Promise<void> =>
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      Promise.reject("plain");
    const { result } = renderHook(() => useAction(reject));
    await act(() => result.current.run());
    expect(result.current.error).toBe("plain");
  });
});

describe("the other methods", () => {
  function answerAll(status = 200, body: unknown = { ok: 1 }) {
    const fetcher = vi.fn<
      (url: string, init?: RequestInit) => Promise<Response>
    >((url) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            url === "/__connection" ? { base: "http://b", token: "t" } : body,
          ),
          { status: url === "/__connection" ? 200 : status },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    return fetcher;
  }

  it("send their method and body", async () => {
    const fetcher = answerAll();
    await patch("/a", { x: 1 });
    await put("/a", { x: 2 });
    await del("/a");
    const methods = fetcher.mock.calls
      .filter(([url]) => url !== "/__connection")
      .map(([, init]) => init?.method);
    expect(methods).toEqual(["PATCH", "PUT", "DELETE"]);
  });

  it("say why for a list of validation errors, and for no reason at all", async () => {
    answerAll(422, { detail: [{ msg: "bad" }] });
    await expect(get("/a")).rejects.toThrow("isn't valid");
    answerAll(500, {});
    await expect(get("/a")).rejects.toThrow("Something went wrong");
  });
});
