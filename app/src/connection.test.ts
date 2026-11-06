import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  forgetConnection,
  get,
  getBytes,
  parseEvents,
  post,
  stream,
} from "./connection";

function fetchWith(answers: Record<string, () => Response>) {
  return vi.fn((url: string) => {
    const answer = new Map(Object.entries(answers)).get(url);
    if (!answer) throw new Error(`unexpected ${url}`);
    return Promise.resolve(answer());
  });
}

const CONNECTION = () =>
  new Response(JSON.stringify({ base: "http://b", token: "tok" }));

afterEach(() => {
  forgetConnection();
  vi.unstubAllGlobals();
});

describe("requests", () => {
  it("sends the token in a header, never in the URL", async () => {
    const fetcher = fetchWith({
      "/__connection": CONNECTION,
      "http://b/api/x": () => new Response(JSON.stringify({ ok: 1 })),
    });
    vi.stubGlobal("fetch", fetcher);
    await expect(get("/x")).resolves.toEqual({ ok: 1 });
    const [url, init] = fetcher.mock.calls[1] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).not.toContain("tok");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
  });

  it("turns a refusal into an error with its reason", async () => {
    vi.stubGlobal(
      "fetch",
      fetchWith({
        "/__connection": CONNECTION,
        "http://b/api/x": () =>
          new Response(JSON.stringify({ detail: "Not now." }), { status: 409 }),
      }),
    );
    const failure = await post("/x", {}).catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(409);
    expect((failure as ApiError).message).toBe("Not now.");
  });

  it("reads bytes", async () => {
    vi.stubGlobal(
      "fetch",
      fetchWith({
        "/__connection": CONNECTION,
        "http://b/api/f": () => new Response(new Uint8Array([1, 2])),
      }),
    );
    expect((await getBytes("/f")).byteLength).toBe(2);
  });
});

describe("event streams", () => {
  it("splits events and keeps what is incomplete", () => {
    const { events, rest } = parseEvents(
      'event: step\ndata: {"n":1}\n\ndata: {"n":2}\n\nevent: to',
    );
    expect(events).toEqual([
      { event: "step", data: { n: 1 } },
      { event: "message", data: { n: 2 } },
    ]);
    expect(rest).toBe("event: to");
  });

  it("hands over events as they arrive", async () => {
    vi.stubGlobal(
      "fetch",
      fetchWith({
        "/__connection": CONNECTION,
        "http://b/api/s": () => new Response('event: done\ndata: {"a":1}\n\n'),
      }),
    );
    const seen: unknown[] = [];
    await stream("/s", (event) => seen.push(event.event));
    expect(seen).toEqual(["done"]);
  });
});

describe("stream edges", () => {
  it("sends a POST body, and a stream with no body just ends", async () => {
    const fetcher = fetchWith({
      "/__connection": CONNECTION,
      "http://b/api/s": () => new Response(null),
    });
    vi.stubGlobal("fetch", fetcher);
    const seen: unknown[] = [];
    await stream("/s", (event) => seen.push(event), {
      method: "POST",
      body: { a: 1 },
    });
    expect(seen).toEqual([]);
  });

  it("skips a part with no data", () => {
    expect(parseEvents("event: x\n\n").events).toEqual([]);
  });
});
