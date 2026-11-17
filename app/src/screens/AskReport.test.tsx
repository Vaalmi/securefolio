import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, DETAIL, DOC, refusal } from "../fakeBackend";
import { installBrowser, open } from "../testApp";
import { ANSWER, chat, THREAD, turn } from "./askFixtures";

// "This was wrong" saves an answer as a question in the owner's own set: the
// document, cited ones first, the place by its kind, or "It isn't in my library",
// the fact and a note. It says when the report is Private, and why.

beforeEach(() => {
  installBrowser();
});

const REPORT = "/threads/t1/messages/m2/report";
const LEASE = { ...DETAIL, document: { ...DOC, pages: 12 } };

function reports(): unknown[] {
  return calls
    .filter((c) => c.method === "POST" && c.path === REPORT)
    .map((c) => c.body);
}

async function openForm(): Promise<HTMLElement> {
  const article = await screen.findByRole("article", { name: /^Answer/ });
  await userEvent.click(
    within(article).getByRole("button", { name: "This was wrong" }),
  );
  return screen.findByRole("form", { name: "This was wrong" });
}

describe("This was wrong", () => {
  it("starts at the cited document and its page, and saves on this Mac", async () => {
    chat(ANSWER, {
      "GET /documents/d1": LEASE,
      [`POST ${REPORT}`]: { id: "r-1", private: false, why: null },
    });
    open("/ask/t1");
    const form = await openForm();
    expect(within(form).getByLabelText("Document")).toHaveValue("d1");
    expect(await within(form).findByLabelText("Page")).toHaveValue("6");
    expect(form).toHaveTextContent("of 12");
    expect(form).toHaveTextContent(
      "Saved on this Mac, in your library's evaluation folder",
    );
    expect(form).toHaveTextContent("passes only if it cites this page");
    await userEvent.type(within(form).getByLabelText(/The fact/), "One cat");
    await userEvent.type(within(form).getByLabelText(/Note/), "It said none.");
    await userEvent.click(
      within(form).getByRole("button", { name: "Save report" }),
    );
    expect(
      await screen.findByText(
        "Saved as a question in your own set, on this Mac.",
      ),
    ).toBeVisible();
    expect(reports()).toEqual([
      {
        document_id: "d1",
        place: { kind: "page", page: 6 },
        fact: "One cat",
        note: "It said none.",
      },
    ]);
    const button = screen.getByRole("button", { name: "Reported wrong" });
    expect(button).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => {
      expect(button).toHaveFocus();
    });
    await userEvent.click(button);
    expect(screen.queryByRole("form", { name: "This was wrong" })).toBeNull();
  });

  it("offers a list's documents first, as its rows cite them", async () => {
    // A list's citations are its
    // table's rows, so its documents are cited ones, not found only by a search.
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
        pending: [],
        unchecked_pending: 0,
        shown_to_model: 1,
      },
    });
    open("/ask/t1");
    const form = await openForm();
    const cited = within(form).getByRole("group", {
      name: "Cited in this answer",
    });
    expect(
      within(cited)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Auto policy"]);
  });

  it("saves a refusal as a question not in the library", async () => {
    chat(
      { ...ANSWER, kind: "no_evidence", parts: undefined, citations: [] },
      { [`POST ${REPORT}`]: { id: "r-1", private: false, why: null } },
    );
    open("/ask/t1");
    const form = await openForm();
    expect(within(form).getByLabelText("Document")).toHaveValue("");
    await userEvent.click(
      within(form).getByRole("radio", { name: "It isn't in my library" }),
    );
    expect(within(form).queryByLabelText("Document")).toBeNull();
    expect(form).toHaveTextContent(
      "passes only if it says it couldn't find enough evidence",
    );
    await userEvent.click(
      within(form).getByRole("button", { name: "Save report" }),
    );
    await waitFor(() => {
      expect(reports()).toEqual([
        { document_id: null, place: null, fact: "", note: "" },
      ]);
    });
  });

  it("says a Private chat's report is Private, and why", async () => {
    chat(ANSWER, {
      "GET /documents/d1": LEASE,
      "GET /threads/t1/messages/m2/report/privacy": {
        why: "this chat is Private",
      },
    });
    open("/ask/t1");
    const form = await openForm();
    await waitFor(() => {
      expect(form).toHaveTextContent(
        "Private, because this chat is Private. It's answered and scored only on this Mac, and never judged by a cloud model.",
      );
    });
  });

  it("asks for the fact when the document has no pages to point to", async () => {
    chat(ANSWER, {
      "GET /documents/d1": {
        ...DETAIL,
        document: { ...DOC, file_kind: "DOCX", pages: null },
      },
    });
    open("/ask/t1");
    const form = await openForm();
    await waitFor(() => {
      expect(form).toHaveTextContent("This document has no pages to point to");
    });
    expect(within(form).queryByLabelText("Page")).toBeNull();
    await userEvent.click(
      within(form).getByRole("button", { name: "Save report" }),
    );
    expect(within(form).getByRole("alert")).toHaveTextContent(
      "Type the fact the answer should have held.",
    );
    expect(reports()).toEqual([]);
  });

  it("asks for a place it can read", async () => {
    chat(ANSWER, { "GET /documents/d1": LEASE });
    open("/ask/t1");
    const form = await openForm();
    const page = await within(form).findByLabelText("Page");
    await userEvent.clear(page);
    await userEvent.type(page, "six");
    await userEvent.click(
      within(form).getByRole("button", { name: "Save report" }),
    );
    expect(within(form).getByRole("alert")).toHaveTextContent(
      "Say which page hold the right answer.",
    );
    expect(reports()).toEqual([]);
  });

  it("shows the backend's refusal", async () => {
    chat(ANSWER, {
      "GET /documents/d1": LEASE,
      [`POST ${REPORT}`]: refusal("Apartment lease has no page 6.", 422),
    });
    open("/ask/t1");
    const form = await openForm();
    await within(form).findByLabelText("Page");
    await userEvent.click(
      within(form).getByRole("button", { name: "Save report" }),
    );
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Apartment lease has no page 6.",
    );
    expect(
      screen.getByRole("button", { name: "This was wrong" }),
    ).toBeVisible();
  });
});

describe("This was wrong's form", () => {
  it("keeps a document picked from a search listed, and saves that one", async () => {
    const roof = { ...DOC, id: "d9", title: "Roof report", pages: 4 };
    let results = [roof];
    chat(ANSWER, {
      "GET /documents/d1": LEASE,
      "GET /documents/d9": { ...DETAIL, document: roof },
      "GET /search": () => ({
        documents: results,
        passages: [],
        hidden_private: 0,
        semantic: true,
      }),
      [`POST ${REPORT}`]: { id: "r-1", private: false, why: null },
    });
    open("/ask/t1");
    const form = await openForm();
    const find = within(form).getByLabelText("Another document");
    await userEvent.type(find, "Roof");
    await within(form).findByRole("option", { name: "Roof report" });
    const select = within(form).getByLabelText("Document");
    await userEvent.selectOptions(select, "d9");
    results = [{ ...DOC, id: "d8", title: "Energy audit" }];
    await userEvent.clear(find);
    await userEvent.type(find, "Energy");
    await within(form).findByRole("option", { name: "Energy audit" });
    expect(select).toHaveValue("d9");
    expect(
      within(form).getByRole("option", { name: "Roof report" }),
    ).toBeInTheDocument();
    await userEvent.type(await within(form).findByLabelText("Page"), "2");
    await userEvent.click(
      within(form).getByRole("button", { name: "Save report" }),
    );
    await waitFor(() => {
      expect(reports()).toEqual([
        {
          document_id: "d9",
          place: { kind: "page", page: 2 },
          fact: "",
          note: "",
        },
      ]);
    });
  });

  it("never lets an earlier search's late reply replace the current one's", async () => {
    const pending: ((reply: Response) => void)[] = [];
    const reply = (documents: object[]): Response =>
      new Response(
        JSON.stringify({
          documents,
          passages: [],
          hidden_private: 0,
          semantic: true,
        }),
      );
    chat(ANSWER, {
      "GET /documents/d1": LEASE,
      "GET /search": () =>
        new Promise<Response>((resolve) => {
          pending.push(resolve);
        }),
    });
    open("/ask/t1");
    const form = await openForm();
    const find = within(form).getByLabelText("Another document");
    await userEvent.type(find, "Roof");
    await userEvent.clear(find);
    await userEvent.type(find, "Energy");
    await waitFor(() => {
      expect(pending.length).toBeGreaterThan(1);
    });
    pending.pop()?.(reply([{ ...DOC, id: "d8", title: "Energy audit" }]));
    await within(form).findByRole("option", { name: "Energy audit" });
    await act(async () => {
      for (const earlier of pending)
        earlier(reply([{ ...DOC, id: "d9", title: "Roof report" }]));
      await new Promise((settled) => setTimeout(settled, 50));
    });
    expect(
      within(form).getByRole("option", { name: "Energy audit" }),
    ).toBeInTheDocument();
    expect(
      within(form).queryByRole("option", { name: "Roof report" }),
    ).toBeNull();
  });

  it("closes on Escape, giving focus back to the button", async () => {
    chat(ANSWER, { "GET /documents/d1": LEASE });
    open("/ask/t1");
    const form = await openForm();
    await userEvent.click(within(form).getByLabelText(/Note/));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("form", { name: "This was wrong" })).toBeNull();
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "This was wrong" }),
      ).toHaveFocus();
    });
    expect(reports()).toEqual([]);
  });

  it("says an answer reported before is reported", async () => {
    chat(ANSWER, {
      "GET /threads/t1": {
        thread: THREAD,
        messages: turn(ANSWER).map((m) =>
          m.role === "assistant" ? { ...m, reported: true } : m,
        ),
        active_run: null,
      },
    });
    open("/ask/t1");
    const button = await screen.findByRole("button", {
      name: "Reported wrong",
    });
    await userEvent.click(button);
    expect(screen.queryByRole("form", { name: "This was wrong" })).toBeNull();
  });

  it("clears an earlier refusal while it saves again", async () => {
    // Saving runs through useAction, which
    // clears the last failure as it starts, so a stale one isn't shown meanwhile.
    const pending: ((answer: Response) => void)[] = [];
    let posts = 0;
    chat(ANSWER, {
      "GET /documents/d1": LEASE,
      [`POST ${REPORT}`]: () => {
        posts += 1;
        if (posts === 1)
          return refusal("Apartment lease has no page 6.", 422)();
        return new Promise<Response>((resolve) => {
          pending.push(resolve);
        });
      },
    });
    open("/ask/t1");
    const form = await openForm();
    await within(form).findByLabelText("Page");
    const save = within(form).getByRole("button", { name: "Save report" });
    await userEvent.click(save);
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Apartment lease has no page 6.",
    );
    await userEvent.click(save);
    await waitFor(() => {
      expect(save).toBeDisabled();
    });
    expect(within(form).queryByRole("alert")).toBeNull();
    pending.pop()?.(
      new Response(JSON.stringify({ id: "r-1", private: false, why: null })),
    );
    expect(
      await screen.findByText(
        "Saved as a question in your own set, on this Mac.",
      ),
    ).toBeVisible();
  });
});
