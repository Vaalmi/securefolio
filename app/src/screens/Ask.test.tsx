import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BASE, calls, LOCAL_ROUTE } from "../fakeBackend";
import { installBrowser, open, posted } from "../testApp";
import { ANSWER, chat, THREAD, turn } from "./askFixtures";

beforeEach(() => {
  installBrowser();
});

describe("a finished answer", () => {
  it("shows citations, marks what's uncited, and previews a source", async () => {
    chat();
    open("/ask/t1");
    const answer = await screen.findByRole("article", { name: "Answer" });
    expect(within(answer).getByText("Uncited")).toBeVisible();
    expect(within(answer).getByText(/1 cited/)).toBeVisible();
    await userEvent.click(
      within(answer).getByRole("button", { name: /^Citation 1/ }),
    );
    expect(
      await screen.findByRole("heading", { name: "Preview" }),
    ).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Back to sources" }),
    );
    await userEvent.click(
      await screen.findByRole("tab", { name: /Also searched/ }),
    );
    expect(await screen.findByText("Liability covers pets.")).toBeVisible();
    await userEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByText(/Wrote the answer/)).toBeVisible();
  });

  it("retries, re-runs in Deep and saves a note", async () => {
    const run = {
      id: "r9",
      thread_id: "t1",
      state: "running",
      card: null,
      error: null,
    };
    chat(ANSWER, { "POST /threads/t1/messages": run });
    open("/ask/t1");
    await userEvent.click(
      await screen.findByRole("button", { name: "Save as note" }),
    );
    await waitFor(() => {
      expect(posted("/messages/m2/note")).toBe(true);
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Re-run in Deep" }),
    );
    await waitFor(() => {
      const sent = calls.find((c) => c.path === "/threads/t1/messages");
      expect(sent?.body).toMatchObject({ mode: "deep" });
    });
  });

  it("says when there's no evidence, with what was searched", async () => {
    chat({
      ...ANSWER,
      kind: "no_evidence",
      parts: [],
      citations: [],
      coverage: { searched: 12, indexing: 3, private_folders: 2 },
      closest: [
        {
          document_id: "d2",
          title: "Renters policy",
          element_id: "e2",
          label: "Page 2",
          text: "Close, but no.",
        },
      ],
    });
    open("/ask/t1");
    expect(
      await screen.findByText(/couldn't find enough evidence/),
    ).toBeVisible();
    expect(screen.getByText(/3 more are still indexing/)).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: /Ask again when/ }),
    );
    await waitFor(() => {
      expect(posted("/threads/t1/ask-when-indexed")).toBe(true);
    });
    await userEvent.click(
      screen.getByRole("button", { name: /Include Private/ }),
    );
    await waitFor(() => {
      expect(posted("/threads/t1/scope")).toBe(true);
    });
    await userEvent.click(
      screen.getByRole("button", { name: /^Preview Renters/ }),
    );
    expect(
      await screen.findByRole("heading", { name: "Preview" }),
    ).toBeVisible();
  });

  it("works out a list from cards, and names what it couldn't count", async () => {
    chat({
      ...ANSWER,
      kind: "list",
      parts: [{ text: "Two policies." }],
      citations: [],
      table: {
        rows: [
          {
            n: 1,
            document_id: "d1",
            title: "Auto policy",
            doc_date: "2025-01-04",
            amount: 1284,
            amount_text: "$1,284.00",
            summary: "A policy.",
          },
        ],
        total: 1284,
        wants_total: true,
        pending: [
          {
            document_id: "d2",
            title: "Umbrella",
            doc_date: null,
            reason: "pending",
          },
        ],
        unchecked_pending: 4,
        shown_to_model: 1,
      },
    });
    open("/ask/t1");
    expect((await screen.findAllByText("$1,284.00")).length).toBeGreaterThan(0);
    expect(screen.getByText(/1 documents aren't counted yet/)).toBeVisible();
    expect(screen.getByText(/4 more cards/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /^Citation 1/ }));
    expect(
      await screen.findByRole("heading", { name: "Preview" }),
    ).toBeVisible();
  });
});

describe("a chat's own controls", () => {
  it("renames, pins, turns memory off and deletes", async () => {
    chat();
    open("/ask/t1");
    await userEvent.click(
      await screen.findByRole("button", { name: /Memory on/ }),
    );
    await waitFor(() => {
      const sent = calls.find((c) => c.method === "PATCH");
      expect(sent?.body).toEqual({ memory_off: true });
    });
    await userEvent.click(screen.getByRole("button", { name: "Chat actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    await userEvent.type(screen.getByLabelText("Chat name"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await userEvent.click(screen.getByRole("button", { name: "Chat actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Pin" }));
    await userEvent.click(screen.getByRole("button", { name: "Chat actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === "DELETE")).toBe(true);
    });
  });

  it("asks before adding Private to a Standard chat", async () => {
    chat(ANSWER, {
      "POST /threads/t1/scope": (body: { consented: boolean }) => ({
        needs_consent: !body.consented,
        documents: [],
      }),
    });
    open("/ask/t1");
    await userEvent.click(
      await screen.findByRole("button", { name: /^Scope:/ }),
    );
    await userEvent.click(await screen.findByText("All · Private included"));
    await userEvent.click(
      await screen.findByRole("button", { name: /Answer on this Mac/ }),
    );
    await waitFor(() => {
      const sent = calls.filter((c) => c.path === "/threads/t1/scope").at(-1);
      expect(sent?.body).toMatchObject({ consented: true });
    });
  });
});

describe("cards in the chat", () => {
  const paused = (card: object) => ({
    "GET /threads/t1": {
      thread: THREAD,
      messages: [],
      active_run: {
        id: "r1",
        thread_id: "t1",
        state: "paused",
        card,
        error: null,
      },
    },
  });

  it("answers the Private card", async () => {
    const card = {
      kind: "private_in_scope",
      id: "c1",
      documents: [{ id: "d1", title: "Federal return" }],
    };
    chat(ANSWER, {
      ...paused(card),
      "POST /threads/t1/resume": {
        id: "r1",
        thread_id: "t1",
        state: "running",
      },
    });
    open("/ask/t1");
    expect(await screen.findByText(/Federal return/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Leave it out" }));
    await waitFor(() => {
      const sent = calls.find((c) => c.path === "/threads/t1/resume");
      expect(sent?.body).toEqual({ card_id: "c1", choice: "leave" });
    });
  });

  it("answers the Deep card, with its cost", async () => {
    const card = {
      kind: "deep_cost",
      id: "c2",
      question: "All of it?",
      estimate_usd: 0.74,
      documents: 42,
      spent_usd: 4.82,
      cap_usd: 25,
    };
    chat(ANSWER, {
      ...paused(card),
      "POST /threads/t1/resume": {
        id: "r1",
        thread_id: "t1",
        state: "running",
      },
    });
    open("/ask/t1");
    expect(
      await screen.findByRole("button", { name: /Run Deep · about \$0.74/ }),
    ).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Use Standard instead" }),
    );
    await waitFor(() => {
      const sent = calls.find((c) => c.path === "/threads/t1/resume");
      expect(sent?.body).toEqual({ card_id: "c2", choice: "standard" });
    });
  });

  it("cancels the Deep card by stopping", async () => {
    const card = {
      kind: "deep_cost",
      id: "c2",
      question: "All of it?",
      estimate_usd: 0.74,
      documents: 42,
      spent_usd: 4.82,
      cap_usd: 25,
    };
    chat(ANSWER, paused(card));
    open("/ask/t1");
    await userEvent.click(
      await screen.findByRole("button", { name: "Cancel" }),
    );
    await waitFor(() => {
      expect(posted("/threads/t1/stop")).toBe(true);
    });
  });
});

describe("following a run", () => {
  it("streams steps and words, and stops", async () => {
    vi.useRealTimers();
    const events = [
      'event: step\ndata: {"name":"searching","detail":""}\n\n',
      'event: step\ndata: {"name":"writing","detail":""}\n\n',
      'event: token\ndata: {"text":"Partly written"}\n\n',
    ].join("");
    chat(ANSWER, {
      "GET /threads/t1": {
        thread: THREAD,
        messages: [turn(ANSWER)[0]],
        active_run: {
          id: "r1",
          thread_id: "t1",
          state: "running",
          card: null,
          error: null,
        },
      },
    });
    const real = vi.mocked(fetch).getMockImplementation();
    vi.mocked(fetch).mockImplementation(
      (url: RequestInfo | URL, init?: RequestInit) => {
        if (url === `${BASE}/api/runs/r1/events?after=0`) {
          const stream = new ReadableStream({
            start(controller) {
              controller.enqueue(new TextEncoder().encode(events));
            },
          });
          return Promise.resolve(new Response(stream));
        }
        return real ? real(url, init) : Promise.reject(new Error("no"));
      },
    );
    open("/ask/t1");
    expect(await screen.findByText("Partly written")).toBeVisible();
    expect(screen.getByText("Writing the answer")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => {
      expect(posted("/threads/t1/stop")).toBe(true);
    });
  });

  it("asks from a new chat, with the scope in the URL, and sends the question", async () => {
    const run = {
      id: "r9",
      thread_id: "t1",
      state: "running",
      card: null,
      error: null,
    };
    chat(ANSWER, {
      "POST /threads": THREAD,
      "POST /threads/t1/messages": run,
    });
    open("/ask/new?scope=sel:d1,d2&q=Compare");
    await waitFor(() => {
      const made = calls.find(
        (c) => c.path === "/threads" && c.method === "POST",
      );
      expect(made?.body).toMatchObject({
        scope: { kind: "documents", ids: ["d1", "d2"] },
      });
    });
    await waitFor(() => {
      expect(posted("/threads/t1/messages")).toBe(true);
    });
  });

  it("offers a first question on an empty chat, in each scope", async () => {
    chat();
    const first = open("/ask/new");
    expect(
      await screen.findByRole("heading", { name: "Ask your library" }),
    ).toBeVisible();
    first.unmount();
    chat();
    open("/ask/new?scope=sel:d1");
    expect(
      await screen.findByRole("heading", { name: "Ask about this document" }),
    ).toBeVisible();
    await userEvent.type(
      screen.getByLabelText("Ask a question"),
      "Who?{Enter}",
    );
    await waitFor(() => {
      expect(
        calls.some((c) => c.path === "/threads" && c.method === "POST"),
      ).toBe(true);
    });
  });

  it("shows a chat's Private state, and refuses show plainly", async () => {
    chat(ANSWER, { "GET /ask/route": LOCAL_ROUTE }, { private_tainted: true });
    open("/ask/t1");
    expect((await screen.findAllByText("Private chat")).length).toBeGreaterThan(
      0,
    );
    expect(
      await screen.findByText(/Nothing in this chat leaves your Mac/),
    ).toBeVisible();
  });
});

describe("moving around a chat", () => {
  it("opens at the newest turn once the chat has loaded", async () => {
    // jsdom lays nothing out, so each answer stands for 300 px of chat.
    const height = vi
      .spyOn(HTMLElement.prototype, "scrollHeight", "get")
      .mockImplementation(function (this: HTMLElement) {
        return this.querySelectorAll("article").length * 300;
      });
    chat();
    const { container } = open("/ask/t1?cite=m2-1&panel=1");
    await screen.findByRole("article", { name: "Answer" });
    const pane = container.querySelector<HTMLElement>(".ask-pane");
    expect(pane?.scrollTop).toBe(300);
    height.mockRestore();
  });

  it("leaves the owner where they scrolled to when an answer finishes", async () => {
    const height = vi
      .spyOn(HTMLElement.prototype, "scrollHeight", "get")
      .mockImplementation(function (this: HTMLElement) {
        return this.querySelectorAll("article").length * 300;
      });
    const asked = {
      id: "m3",
      role: "user",
      content: "And dogs?",
      answer: null,
      created_at: 1_700_000_001,
    };
    const answered = { ...asked, id: "m4", role: "assistant", answer: ANSWER };
    const run = { id: "r1", thread_id: "t1", state: "running", card: null };
    let finish = (): void => undefined;
    let reads = 0;
    chat(ANSWER, {
      "GET /threads/t1": () => {
        reads += 1;
        const done = reads > 1;
        return {
          thread: THREAD,
          messages: [...turn(ANSWER), asked, ...(done ? [answered] : [])],
          active_run: done ? null : run,
          last_failure: null,
        };
      },
      "GET /runs/r1/events": () =>
        new Response(
          new ReadableStream({
            start(controller) {
              finish = () => {
                controller.enqueue(
                  new TextEncoder().encode(
                    'id: 1\nevent: done\ndata: {"state":"completed"}\n\n',
                  ),
                );
                controller.close();
              };
            },
          }),
        ),
    });
    const { container } = open("/ask/t1");
    await screen.findByRole("article", { name: "Answer in progress" });
    const pane = container.querySelector<HTMLElement>(".ask-pane");
    expect(pane?.scrollTop).toBe(600);
    // The owner scrolls up to read the first answer while the second is written.
    if (pane) pane.scrollTop = 0;
    pane?.dispatchEvent(new Event("scroll"));
    finish();
    await waitFor(() => {
      expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(
        2,
      );
    });
    expect(pane?.scrollTop).toBe(0);
    height.mockRestore();
  });

  it("keeps one panel in the address however many citations are opened", async () => {
    chat();
    open("/ask/t1");
    const answer = await screen.findByRole("article", { name: "Answer" });
    const chip = within(answer).getByRole("button", { name: /^Citation 1/ });
    await userEvent.click(chip);
    await userEvent.click(chip);
    await userEvent.click(
      await screen.findByRole("button", { name: "Back to sources" }),
    );
    await userEvent.click(chip);
    const params = new URLSearchParams(window.location.hash.split("?")[1]);
    expect(params.getAll("panel")).toEqual(["1"]);
    expect(params.get("cite")).toBe("m2-1");
  });
});
