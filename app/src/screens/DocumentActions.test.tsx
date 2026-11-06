import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import {
  calls,
  CARD,
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

const element = (id: string, text: string, locator: object, kind = "text") => ({
  id,
  ordinal: 0,
  kind,
  text,
  locator,
  label: "Here",
  reading_state: null,
  reading_reason: null,
});

function doc(kind: string, extra = {}, detail = {}) {
  fakeBackend({
    "GET /documents/d1": {
      ...DETAIL,
      document: { ...DOC, file_kind: kind },
      ...detail,
    },
    "GET /documents/d1/file": new ArrayBuffer(4),
    "GET /documents/d1/preview": new ArrayBuffer(4),
    "GET /documents/d1/cloud-read-page": PAGE_COST,
    ...extra,
  });
}

describe("Document actions", () => {
  it("re-reads a page, and sends one to Cloud AI only after the cost is shown", async () => {
    doc("PDF");
    open("/doc/d1");
    const more = await screen.findByRole("button", {
      name: "More document actions",
    });
    await userEvent.click(more);
    await userEvent.click(
      screen.getByRole("menuitem", { name: "Re-read this page" }),
    );
    await waitFor(() => {
      expect(posted("/documents/d1/reread-page")).toBe(true);
    });
    await userEvent.click(more);
    await userEvent.click(
      await screen.findByRole("menuitem", {
        name: "Read this page with Cloud AI…",
      }),
    );
    expect(posted("/documents/d1/cloud-read-page")).toBe(false);
    const dialog = screen.getByRole("dialog", {
      name: "Read page 1 with Cloud AI?",
    });
    expect(dialog).toHaveTextContent(
      "to Cloud AI: GPT-6.1 Sol, from OpenAI ($2.00 in · $10.00 out per million tokens)",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: /at most \$0.085/ }),
    );
    await waitFor(() => {
      expect(posted("/documents/d1/cloud-read-page")).toBe(true);
    });
    // The model and price shown go with it, so the backend sends it at those.
    const sent = calls.find(
      (call) =>
        call.method === "POST" && call.path === "/documents/d1/cloud-read-page",
    );
    // And the revision shown, so a page reindexed since isn't sent for it.
    expect(sent?.body).toEqual({
      page: 1,
      model: "openai:gpt-6.1-sol",
      input_usd: 2,
      output_usd: 10,
      revision_id: "r1",
    });
  });

  it("shows why a page wasn't sent, and the cost as it is now", async () => {
    let asked = 0;
    const later = {
      ...PAGE_COST,
      model: "openai:gpt-6-astra",
      model_name: "GPT-6 Astra",
      input_usd: 20,
      output_usd: 100,
      cost_usd: 0.85,
    };
    doc("PDF", {
      "GET /documents/d1/cloud-read-page": () =>
        asked++ === 0 ? PAGE_COST : later,
      "POST /documents/d1/cloud-read-page": refusal(
        "The page model or its price has changed since its cost was shown.",
      ),
    });
    open("/doc/d1");
    await userEvent.click(
      await screen.findByRole("button", { name: "More document actions" }),
    );
    await userEvent.click(
      await screen.findByRole("menuitem", {
        name: "Read this page with Cloud AI…",
      }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Read page 1 with Cloud AI?",
    });
    await userEvent.click(
      within(dialog).getByRole("button", { name: /at most \$0.085/ }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The page model or its price has changed",
    );
    expect(
      await within(dialog).findByRole("button", { name: /at most \$0.850/ }),
    ).toBeInTheDocument();
    expect(dialog).toHaveTextContent("GPT-6 Astra, from OpenAI");
  });

  it("doesn't offer the cloud read for a Private document", async () => {
    doc(
      "PDF",
      { "GET /documents/d1/cloud-read-page": refusal("Private.") },
      {
        document: { ...DOC, sensitivity: "private" },
        cloud_page_allowed: false,
      },
    );
    open("/doc/d1");
    await userEvent.click(
      await screen.findByRole("button", { name: "More document actions" }),
    );
    expect(
      screen.getByRole("menuitem", { name: "Re-read this page" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("menuitem", { name: /with Cloud AI/ }),
    ).toBeNull();
  });

  it("shows the cloud read as unavailable, saying why, and Re-read still works", async () => {
    const why =
      "GPT-6.1 Sol needs an OpenAI key (or its Project ID). Add it in Settings, or choose another model.";
    doc("PDF", {
      "GET /documents/d1/cloud-read-page": {
        ...PAGE_COST,
        cost_usd: null,
        unavailable: why,
      },
    });
    open("/doc/d1");
    const more = await screen.findByRole("button", {
      name: "More document actions",
    });
    await userEvent.click(more);
    await userEvent.click(
      await screen.findByRole("menuitem", {
        name: "Read this page with Cloud AI (unavailable)…",
      }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Reading a page in the cloud is unavailable",
    });
    expect(dialog).toHaveTextContent(why);
    expect(dialog).toHaveTextContent(
      "Re-read this page, on this Mac, still works.",
    );
    expect(within(dialog).queryByRole("button", { name: /Send/ })).toBeNull();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await userEvent.click(more);
    await userEvent.click(
      screen.getByRole("menuitem", { name: "Re-read this page" }),
    );
    await waitFor(() => {
      expect(posted("/documents/d1/reread-page")).toBe(true);
    });
    expect(posted("/documents/d1/cloud-read-page")).toBe(false);
  });

  it("removes and trashes after confirming, and re-indexes", async () => {
    doc("PDF");
    open("/doc/d1");
    const more = await screen.findByRole("button", {
      name: "More document actions",
    });
    await userEvent.click(more);
    await userEvent.click(screen.getByRole("menuitem", { name: "Re-index" }));
    await waitFor(() => {
      expect(posted("/documents/d1/reindex")).toBe(true);
    });
    await userEvent.click(more);
    await userEvent.click(
      screen.getByRole("menuitem", { name: "Move copy to Trash…" }),
    );
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Move copy to Trash",
      }),
    );
    await waitFor(() => {
      expect(posted("/documents/d1/trash-managed-copy")).toBe(true);
    });
  });
});

describe("more Document actions", () => {
  it("edits the card, marks what changed, and manages collections", async () => {
    doc("PDF", { "PATCH /documents/d1/card": CARD });
    open("/doc/d1");
    expect(await screen.findByText("Edited by you: Summary")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Edit card" }));
    await userEvent.click(screen.getByRole("button", { name: "Save card" }));
    expect(await screen.findByText("No changes.")).toBeVisible();
    await userEvent.click(
      await screen.findByRole("button", { name: "Edit card" }),
    );
    await userEvent.clear(screen.getByLabelText("Tags"));
    await userEvent.type(screen.getByLabelText("Tags"), "a, b");
    await userEvent.type(screen.getByLabelText("Dates"), "\nEnds: Sep 1");
    await userEvent.type(screen.getByLabelText("People"), "\nSam");
    await userEvent.type(screen.getByLabelText("Organizations"), "\nAcme");
    await userEvent.type(screen.getByLabelText("Amounts"), "\n$1");
    await userEvent.type(screen.getByLabelText("Title"), "!");
    await userEvent.type(screen.getByLabelText("Summary"), "!");
    await userEvent.selectOptions(screen.getByLabelText("Type"), "Report");
    await userEvent.click(screen.getByRole("button", { name: "Save card" }));
    await waitFor(() => {
      const edit = calls.find((c) => c.method === "PATCH");
      expect(edit?.body).toMatchObject({
        tags: ["a", "b"],
        doc_type: "Report",
        dates: expect.arrayContaining([
          { label: "Ends", value: "Sep 1" },
        ]) as unknown,
      });
    });
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Remove from collection House",
      }),
    );
    await waitFor(() => {
      expect(posted("/collections/c1/remove")).toBe(true);
    });
  });

  it("marks a basic card as pending, and a missing one as none", async () => {
    doc("PDF", {}, { card: { ...CARD, state: "pending", edited_fields: [] } });
    const first = open("/doc/d1");
    expect((await screen.findAllByText("Card pending")).length).toBeGreaterThan(
      0,
    );
    first.unmount();
    doc("PDF", {}, { card: null });
    open("/doc/d1");
    expect(
      await screen.findByText("This document has no card yet."),
    ).toBeVisible();
  });

  it("writes, edits and deletes a note, and opens its citation", async () => {
    const note = {
      id: "n1",
      title: "Renew",
      body: "Renew by June.",
      citations: [
        {
          document_id: "d1",
          revision_id: "r1",
          element_id: "e1",
          opens: {
            state: "current",
            element: element("e1", "x", { kind: "lines", start: 1, end: 1 }),
          },
        },
      ],
      document_id: "d1",
      source_label: "Written by you",
      sensitivity: "standard",
      created_at: 1_700_000_000,
    };
    doc("PDF", { "GET /notes": [note], "POST /notes": note });
    open("/doc/d1");
    await userEvent.click(await screen.findByRole("tab", { name: /Notes/ }));
    expect(await screen.findByText(/Renew by June/)).toBeVisible();
    await userEvent.type(screen.getByLabelText("Text"), "More.");
    await userEvent.click(screen.getByRole("button", { name: "Save note" }));
    await waitFor(() => {
      expect(posted("/notes")).toBe(true);
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Edit note Renew" }),
    );
    const form = screen.getByRole("form", { name: "Edit note Renew" });
    await userEvent.type(within(form).getByLabelText("Text"), "!");
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(posted("/notes/n1")).toBe(true);
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "Delete note Renew" }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^Citation 1/ }));
    await waitFor(() => {
      expect(window.location.hash).toContain("el=e1");
    });
  });

  it("shows a failed load, and stars", async () => {
    fakeBackend({ "GET /documents/d1": refusal("Gone.", 404) });
    const first = open("/doc/d1");
    expect(await screen.findByText("Gone.")).toBeVisible();
    first.unmount();
    doc("PDF");
    open("/doc/d1");
    await userEvent.click(await screen.findByRole("button", { name: "Star" }));
    await waitFor(() => {
      expect(posted("/documents/d1/star")).toBe(true);
    });
  });
});
