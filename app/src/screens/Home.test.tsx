import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  calls,
  DETAIL,
  DOC,
  fakeBackend,
  PAGE_COST,
  refusal,
} from "../fakeBackend";
import { installBrowser, open, posted } from "../testApp";

beforeEach(() => {
  installBrowser();
});

describe("Home", () => {
  it("shows the lists and the indexing line", async () => {
    fakeBackend({
      "GET /jobs": [
        {
          id: "j",
          job_id: "j",
          document_id: "d9",
          file: "Broken.pdf",
          folder: "Home",
          status: "Failed",
          detail: "Couldn't read it.",
          skip_kind: null,
        },
      ],
    });
    open("/home");
    expect(
      await screen.findByRole("heading", { name: "Recents" }),
    ).toBeVisible();
    expect(await screen.findAllByText("Apartment lease")).not.toHaveLength(0);
    expect(await screen.findByText(/Search already works/)).toBeVisible();
    const retry = await screen.findAllByRole("button", {
      name: "Retry Broken.pdf",
    });
    await userEvent.click(retry[0]);
    await waitFor(() => {
      expect(posted("/jobs/j/retry")).toBe(true);
    });
  });

  it("offers a first step on an empty library", async () => {
    fakeBackend({
      "GET /library": { rows: [], total: 0, offset: 0 },
      "GET /library/counts": { total: 0, types: {} },
    });
    open("/home");
    expect(await screen.findByText("Your library is empty")).toBeVisible();
  });

  it("offers search and Ask when every document is Private", async () => {
    // Home's lists leave Private out, so they come back empty; the count doesn't.
    fakeBackend({
      "GET /library": { rows: [], total: 0, offset: 0 },
      "GET /library/counts": { total: 3, types: {} },
    });
    open("/home");
    expect(
      await screen.findByRole("combobox", {
        name: "Search or ask your library",
      }),
    ).toBeVisible();
    expect(screen.queryByText("Your library is empty")).toBeNull();
  });

  it("says when the service stopped, and restarts it", async () => {
    fakeBackend({
      "GET /health": { version: "2", database: "ready", worker: "stopped" },
    });
    open("/home");
    await userEvent.click(
      await screen.findByRole("button", { name: "Restart service" }),
    );
    await waitFor(() => {
      expect(posted("/worker/restart")).toBe(true);
    });
  });
});

describe("the search box", () => {
  const passage = {
    id: "p1",
    element_id: "e1",
    snippet: "The monthly rent is due.",
    locator: { kind: "pdf", page: 2, rects: [], precision: "page" },
    label: "Page 2",
    document: DOC,
  };

  it("groups results, opens the chosen one and warms the model", async () => {
    fakeBackend({
      "GET /search": {
        documents: [DOC],
        passages: [passage],
        hidden_private: 0,
        semantic: true,
      },
      "GET /documents/d1": DETAIL,
      "GET /documents/d1/file": new ArrayBuffer(4),
      "GET /documents/d1/cloud-read-page": PAGE_COST,
    });
    open("/home");
    const box = await screen.findByRole("combobox");
    await userEvent.click(box);
    await userEvent.keyboard("rent");
    const results = await screen.findByRole("listbox");
    expect(
      await within(results).findByText("monthly", { exact: false }),
    ).toBeVisible();
    expect(posted("/search/warm")).toBe(true);
    // The first ArrowDown moves to the first result; up again wraps to the passage.
    await userEvent.keyboard("{ArrowDown}{ArrowUp}{Enter}");
    await waitFor(() => {
      expect(window.location.hash).toContain("/doc/d1?el=e1");
    });
  });

  it("merges meaning in once the model has loaded", async () => {
    let asked = 0;
    fakeBackend({
      "GET /search": () => {
        asked += 1;
        return {
          documents: [],
          passages: asked > 1 ? [passage] : [],
          hidden_private: 2,
          semantic: asked > 1,
        };
      },
    });
    open("/home");
    await userEvent.type(await screen.findByRole("combobox"), "rent");
    expect(await screen.findByText(/2 Private documents match/)).toBeVisible();
    expect(
      await screen.findByText(/monthly/, {}, { timeout: 2000 }),
    ).toBeVisible();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("combobox")).toHaveValue("");
  });

  it("shows a refusal", async () => {
    fakeBackend({ "GET /search": refusal("Search stopped.") });
    open("/home");
    await userEvent.type(await screen.findByRole("combobox"), "rent");
    expect(await screen.findByText("Search stopped.")).toBeVisible();
  });

  it("switches scope and mode, and asks the backend for the label", async () => {
    fakeBackend({
      "GET /ask/route": () => ({ local: true, model: "Local model" }),
    });
    open("/home");
    await userEvent.click(
      await screen.findByRole("button", { name: /^Scope:/ }),
    );
    const menu = screen.getByRole("menu", { name: "Scope" });
    expect(within(menu).getByText("Collection: House")).toBeVisible();
    await userEvent.click(within(menu).getByText("All · Private included"));
    expect(
      await screen.findByRole("button", { name: /Ask Local/ }),
    ).toBeDisabled();
    await userEvent.type(screen.getByRole("combobox"), "x");
    expect(
      await screen.findByText("Private included: answers on this Mac"),
    ).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /^Scope:/ }));
    await userEvent.click(screen.getByText("Collection: House"));
    expect(await screen.findByText("Searching House only")).toBeVisible();
    await waitFor(() => {
      expect(calls.some((c) => c.path.includes("collection=c1"))).toBe(true);
    });
  });
});

describe("results that no longer apply", () => {
  it("drops Private results as soon as the scope leaves Private out", async () => {
    const secret = {
      ...DOC,
      id: "p",
      title: "Clinic visit",
      sensitivity: "private",
    };
    fakeBackend({
      "GET /search": () => ({
        documents: [secret],
        passages: [],
        hidden_private: 0,
        semantic: true,
      }),
    });
    open("/home");
    await userEvent.click(
      await screen.findByRole("button", { name: /^Scope:/ }),
    );
    await userEvent.click(screen.getByText("All · Private included"));
    await userEvent.type(screen.getByRole("combobox"), "clinic");
    expect(await screen.findByRole("option")).toHaveTextContent("Clinic visit");
    vi.mocked(fetch).mockImplementation(() => new Promise(() => undefined));
    await userEvent.click(screen.getByRole("button", { name: /^Scope:/ }));
    await userEvent.click(screen.getByText("All · Private excluded"));
    expect(screen.queryByRole("option")).toBeNull();
  });

  it("won't open a result from earlier typing by mouse", async () => {
    let answer = 0;
    fakeBackend({
      "GET /search": () => {
        answer += 1;
        return answer > 1
          ? new Promise(() => undefined)
          : {
              documents: [DOC],
              passages: [],
              hidden_private: 0,
              semantic: true,
            };
      },
    });
    open("/home");
    const box = await screen.findByRole("combobox");
    await userEvent.type(box, "lease");
    const shown = await screen.findByRole("option");
    await userEvent.keyboard("x");
    await userEvent.pointer({ keys: "[MouseLeft>]", target: shown });
    expect(window.location.hash).not.toContain("/doc/");
  });

  it("opens the current result when Return comes before it", async () => {
    let answer = 0;
    const other = { ...DOC, id: "d2", title: "Roof report" };
    fakeBackend({
      "GET /documents/d2": { ...DETAIL, document: other },
      "GET /search": () => {
        answer += 1;
        return {
          documents: [answer > 1 ? other : DOC],
          passages: [],
          hidden_private: 0,
          semantic: true,
        };
      },
    });
    open("/home");
    const box = await screen.findByRole("combobox");
    await userEvent.type(box, "lease");
    await screen.findByRole("option");
    expect(window.location.hash).not.toContain("/doc/");
    await userEvent.keyboard("x{Meta>}{Enter}{/Meta}");
    expect(window.location.hash).not.toContain("/doc/d1");
    await waitFor(() => {
      expect(window.location.hash).toContain("/doc/d2");
    });
  });

  it("drops an open asked for in a scope that was then changed", async () => {
    // The new scope's results opened, though the key was for the
    // last scope's.
    const waiting: (() => void)[] = [];
    fakeBackend({
      "GET /search": () =>
        new Promise<Response>((resolve) => {
          waiting.push(() => {
            resolve(
              new Response(
                JSON.stringify({
                  documents: [DOC],
                  passages: [],
                  hidden_private: 0,
                  semantic: true,
                }),
              ),
            );
          });
        }),
    });
    const searched = (words: string, wide: boolean) =>
      calls.some((call) => {
        const [path, query] = call.path.split("?");
        const asked = new URLSearchParams(query);
        return (
          path === "/search" &&
          asked.get("q") === words &&
          asked.get("include_private") === String(wide)
        );
      });
    open("/home");
    const box = await screen.findByRole("combobox");
    // A slow runner searches part of the word while it's typed; so does this.
    await userEvent.type(box, "lea");
    await waitFor(() => {
      expect(searched("lea", false)).toBe(true);
    });
    await userEvent.type(box, "se");
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    await userEvent.click(screen.getByRole("button", { name: /^Scope: / }));
    await userEvent.click(
      await screen.findByRole("menuitemradio", { name: /Private included/ }),
    );
    await waitFor(() => {
      expect(searched("lease", true)).toBe(true);
    });
    for (const answer of waiting) answer();
    await screen.findByRole("option");
    expect(window.location.hash).not.toContain("/doc/");
  });
});
