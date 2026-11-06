import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, DETAIL, DOC, fakeBackend } from "../fakeBackend";
import { installBrowser, open } from "../testApp";
import { ANSWER, chat, THREAD } from "./askFixtures";

beforeEach(() => {
  installBrowser();
});

describe("what goes wrong in a run", () => {
  it("shows a failed answer and asks it again", async () => {
    const run = {
      id: "r9",
      thread_id: "t1",
      state: "running",
      card: null,
      error: null,
    };
    fakeBackend({
      "GET /threads": [THREAD],
      "GET /threads/t1": {
        thread: THREAD,
        messages: [],
        active_run: null,
        last_failure: {
          error: "The local model isn't installed.",
          question: "Can I keep a cat?",
        },
      },
      "POST /threads/t1/messages": run,
    });
    open("/ask/t1");
    expect(
      await screen.findByText("The local model isn't installed."),
    ).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Ask again" }));
    await waitFor(() => {
      const sent = calls.find((c) => c.path === "/threads/t1/messages");
      expect(sent?.body).toMatchObject({ text: "Can I keep a cat?" });
    });
  });

  it("follows a run again when its stream drops", async () => {
    const running = {
      id: "r1",
      thread_id: "t1",
      state: "running",
      card: null,
      error: null,
    };
    chat(ANSWER, {
      "GET /threads/t1": {
        thread: THREAD,
        messages: [],
        active_run: running,
        last_failure: null,
      },
    });
    open("/ask/t1");
    await waitFor(
      () => {
        const follows = calls.filter((c) =>
          c.path.startsWith("/runs/r1/events"),
        );
        expect(follows.length).toBeGreaterThan(1);
      },
      { timeout: 4000 },
    );
  });
});

describe("a citation's preview", () => {
  const placed = (state: string, locator: object | null) => ({
    "GET /documents/d1/citation": {
      state,
      element: { id: "e9", text: "The text it read then.", locator, label: "" },
    },
  });

  it("says when the file has changed, and highlights nothing", async () => {
    chat(ANSWER, placed("stored", { kind: "pdf", page: 1, rects: [] }));
    open("/ask/t1");
    const answer = await screen.findByRole("article", { name: "Answer" });
    await userEvent.click(
      within(answer).getByRole("button", { name: /^Citation 1/ }),
    );
    expect(
      await screen.findByText(/has changed since the answer/),
    ).toBeVisible();
    expect(screen.getByText("The text it read then.")).toBeVisible();
  });

  it.each([
    ["PNG", "image-page", "pdf-page"],
    ["PDF", "pdf-page", "image-page"],
  ])(
    "draws a %s's citation on the file itself",
    async (kind, shown, notShown) => {
      const area = { x0: 0.1, y0: 0.2, x1: 0.6, y1: 0.3 };
      chat(ANSWER, {
        ...placed("current", {
          kind: "pdf",
          page: 1,
          rects: [area],
          precision: "words",
        }),
        "GET /documents/d1": {
          ...DETAIL,
          document: { ...DOC, file_kind: kind },
        },
      });
      open("/ask/t1");
      const answer = await screen.findByRole("article", { name: "Answer" });
      await userEvent.click(
        within(answer).getByRole("button", { name: /^Citation 1/ }),
      );
      const panel = screen.getByRole("complementary", { name: "Sources" });
      const page = await within(panel).findByTestId(shown);
      expect(page.querySelector("#cited-passage")).not.toBeNull();
      expect(within(panel).queryByTestId(notShown)).toBeNull();
    },
  );
});

describe("copying an answer", () => {
  function copied(): Promise<string> {
    return new Promise((resolve) => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: (text: string) => {
            resolve(text);
            return Promise.resolve();
          },
        },
      });
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    });
  }

  it("keeps its citations", async () => {
    chat();
    open("/ask/t1");
    await screen.findByRole("article", { name: "Answer" });
    const text = await copied();
    expect(text).toContain("[1]");
  });

  it("keeps a list's rows and total", async () => {
    chat({
      ...ANSWER,
      kind: "list",
      parts: [{ text: "One policy." }],
      citations: [],
      table: {
        rows: [
          {
            n: 1,
            document_id: "d1",
            title: "Auto policy",
            doc_date: null,
            amount: 1284,
            amount_text: "$1,284.00",
            summary: "A policy.",
          },
        ],
        total: 1284,
        wants_total: true,
        pending: [],
        unchecked_pending: 0,
        shown_to_model: 1,
      },
    });
    open("/ask/t1");
    await screen.findByRole("article", { name: "Answer" });
    const text = await copied();
    expect(text).toContain("Auto policy, no date: $1,284.00");
    expect(text).toContain("Total: $1,284.00");
  });
});

describe("a stream that ends", () => {
  it("stops following once its run is done", async () => {
    const running = {
      id: "r1",
      thread_id: "t1",
      state: "running",
      card: null,
      error: null,
    };
    let reads = 0;
    const stream = new Response(
      'id: 1\nevent: done\ndata: {"state":"completed"}\n\n',
    );
    chat(ANSWER, {
      "GET /threads/t1": () => {
        reads += 1;
        return {
          thread: THREAD,
          messages: [],
          active_run: reads === 1 ? running : null,
          last_failure: null,
        };
      },
      "GET /runs/r1/events": () => stream.clone(),
    });
    open("/ask/t1");
    await waitFor(() => {
      expect(reads).toBeGreaterThan(1);
    });
    expect(calls.filter((c) => c.path.startsWith("/runs/r1"))).toHaveLength(1);
  });
});

describe("a list's words", () => {
  it("show their citations and mark what's uncited", async () => {
    chat({
      ...ANSWER,
      kind: "list",
      parts: [
        { text: "These are your policies.", cite: [1] },
        { text: "They rose last year.", uncited: true },
      ],
      citations: [
        {
          n: 1,
          document_id: "d1",
          revision_id: "r1",
          element_id: "card:d1",
          title: "Auto policy",
          label: "Card · Amounts",
          quote: "$1,284.00",
          locator: { kind: "card", field: "amounts" },
        },
      ],
      table: {
        rows: [],
        total: 0,
        wants_total: false,
        pending: [],
        unchecked_pending: 0,
        shown_to_model: 1,
      },
    });
    open("/ask/t1");
    const answer = await screen.findByRole("article", { name: "Answer" });
    expect(within(answer).getByText("Uncited")).toBeVisible();
    await userEvent.click(
      within(answer).getByRole("button", { name: /^Citation 1/ }),
    );
    expect(
      await screen.findByRole("heading", { name: "Preview" }),
    ).toBeVisible();
    expect(screen.queryByText(/has changed since the answer/)).toBeNull();
  });
});
