import { screen, waitFor } from "@testing-library/react";
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

describe("Document", () => {
  it("draws a PDF citation from its rectangles", async () => {
    doc("PDF", {
      "GET /documents/d1/elements/e1": {
        current: true,
        element: element("e1", "Rent", {
          kind: "pdf",
          page: 2,
          rects: [{ x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.3 }],
          precision: "words",
        }),
      },
    });
    open("/doc/d1?el=e1");
    const overlay = await screen.findByTestId("pdf-highlight");
    expect(overlay).toHaveClass("pdf-hl");
    expect(overlay.style.left).toBe("10%");
    expect(await screen.findByLabelText("Page")).toHaveValue("2");
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.queryByTestId("pdf-highlight")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    await userEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(screen.getByText("100%")).toBeVisible();
  });

  it("shows an area, not a highlight, for element precision", async () => {
    doc("PDF", {
      "GET /documents/d1/elements/e1": {
        current: true,
        element: element("e1", "Table", {
          kind: "pdf",
          page: 1,
          rects: [{ x0: 0, y0: 0, x1: 1, y1: 1 }],
          precision: "element",
        }),
      },
    });
    open("/doc/d1?el=e1");
    expect(await screen.findByTestId("pdf-highlight")).toHaveClass("pdf-area");
  });

  it.each([
    [
      "XLSX",
      { kind: "sheet", sheet: "Bills", range: "A3:C3" },
      "Rent | 10 | 20",
      "Rent",
    ],
    ["CSV", { kind: "sheet", sheet: "Sheet1", range: "A1:B1" }, "a | b", "a"],
    ["MD", { kind: "lines", start: 5, end: 5 }, "marker clove", "5"],
    ["TXT", { kind: "lines", start: 1, end: 2 }, "one\ntwo", "2"],
    [
      "EML",
      { kind: "message", message: 2, start_line: 1, end_line: 1 },
      "Hello there",
      "Hello there",
    ],
    ["DOCX", { kind: "anchor", anchor: "#p1" }, "A paragraph", "A paragraph"],
    ["HTML", { kind: "anchor", anchor: "#p1" }, "A heading", "A heading"],
  ])("opens %s at its citation", async (kind, locator, text, shown) => {
    const stored = element(
      "e1",
      text,
      locator,
      kind === "HTML" ? "heading" : "text",
    );
    doc(kind, {
      "GET /documents/d1/elements/e1": { current: true, element: stored },
      "GET /documents/d1/text": { elements: [stored], total: 1 },
    });
    open("/doc/d1?el=e1");
    expect(await screen.findByText(shown)).toBeVisible();
    await waitFor(() => {
      expect(document.getElementById("cited-passage")).not.toBeNull();
    });
  });

  it("opens a slide, with its pager", async () => {
    const stored = element("e1", "Dues rise", {
      kind: "slide",
      slide: 2,
      rects: [],
    });
    doc("PPTX", {
      "GET /documents/d1/elements/e1": { current: true, element: stored },
      "GET /documents/d1/pages/2": { elements: [stored], total: 1 },
    });
    open("/doc/d1?el=e1");
    expect(await screen.findByText("Dues rise")).toHaveClass("hl");
    expect(screen.getByLabelText("Slide")).toHaveValue("2");
  });

  it.each(["PNG", "HEIC"])("shows a %s", async (kind) => {
    doc(kind);
    open("/doc/d1");
    expect(await screen.findByTestId("image-page")).toBeVisible();
    await waitFor(() => {
      expect(screen.getByRole("img")).toHaveAttribute("src");
    });
    const wanted = kind === "HEIC" ? "/preview" : "/file";
    expect(calls.some((c) => c.path.endsWith(wanted))).toBe(true);
  });
});

describe("more Document", () => {
  it("explains a changed original, and indexes the new version", async () => {
    const stored = element("e1", "The stored words", {
      kind: "pdf",
      page: 1,
      rects: [],
      precision: "page",
    });
    doc(
      "PDF",
      { "GET /documents/d1/pages/1": { elements: [stored], total: 1 } },
      { view_state: "changed" },
    );
    open("/doc/d1");
    expect(
      await screen.findByText("The original file changed after it was indexed"),
    ).toBeVisible();
    expect(await screen.findByText("The stored words")).toBeVisible();
    expect(screen.queryByTestId("pdf-canvas")).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Index the new version" }),
    );
    await waitFor(() => {
      expect(posted("/documents/d1/reindex")).toBe(true);
    });
  });

  it("explains a missing original, and locates it", async () => {
    window.securefolioChosenPaths = ["/moved/a.pdf"];
    doc(
      "PDF",
      { "POST /documents/d1/relocate": refusal("That isn't the same file.") },
      { view_state: "missing" },
    );
    open("/doc/d1");
    await userEvent.click(
      await screen.findByRole("button", { name: "Locate file…" }),
    );
    expect(await screen.findByText("That isn't the same file.")).toBeVisible();
    delete window.securefolioChosenPaths;
  });

  it("offers Download & Index for a file only in the cloud", async () => {
    doc("PDF", {}, { view_state: "not_downloaded" });
    open("/doc/d1");
    await userEvent.click(
      await screen.findByRole("button", { name: "Download & Index" }),
    );
    await waitFor(() => {
      expect(posted("/documents/d1/download")).toBe(true);
    });
  });

  it("shows stored text for an evicted file, and says when it isn't indexed", async () => {
    doc(
      "PDF",
      { "GET /documents/d1/text": { elements: [], total: 0 } },
      { view_state: "stored" },
    );
    const first = open("/doc/d1");
    expect(await screen.findByLabelText("Stored text")).toBeVisible();
    first.unmount();
    doc(
      "PDF",
      {},
      {
        document: {
          ...DOC,
          active_revision_id: null,
          status_reason: "Waiting.",
        },
      },
    );
    open("/doc/d1");
    expect(await screen.findByText("Waiting.")).toBeVisible();
  });

  it("says a citation is from an earlier version, with no highlight", async () => {
    doc("TXT", {
      "GET /documents/d1/elements/e1": {
        current: false,
        element: element("e1", "Older words", {
          kind: "lines",
          start: 1,
          end: 1,
        }),
      },
      "GET /documents/d1/text": { elements: [], total: 0 },
    });
    open("/doc/d1?el=e1");
    expect(
      await screen.findByText(/earlier version. Older words/),
    ).toBeVisible();
    expect(document.getElementById("cited-passage")).toBeNull();
  });
});

describe("long documents, and following a job", () => {
  const row = (ordinal: number) => ({
    id: `e${String(ordinal)}`,
    ordinal,
    kind: "text",
    text: `row ${String(ordinal)} | 1`,
    locator: {
      kind: "sheet",
      sheet: "Bills",
      range: `A${String(ordinal + 1)}:B${String(ordinal + 1)}`,
    },
    label: "Here",
    reading_state: null,
    reading_reason: null,
  });

  it("opens the window holding a citation, and moves between windows", async () => {
    const cited = row(4500);
    fakeBackend({
      "GET /documents/d1": {
        ...DETAIL,
        document: { ...DOC, file_kind: "XLSX" },
      },
      "GET /documents/d1/elements/e4500": { current: true, element: cited },
      "GET /documents/d1/sheets": [
        { sheet: "Bills", first_ordinal: 0 },
        { sheet: "Income", first_ordinal: 9000 },
      ],
      "GET /documents/d1/text": { elements: [cited], total: 9500 },
    });
    open("/doc/d1?el=e4500");
    expect(await screen.findByRole("status", { name: "" })).toBeDefined();
    await waitFor(() => {
      expect(calls.some((c) => c.path.includes("/text?offset=4000"))).toBe(
        true,
      );
    });
    expect(await screen.findByText("4,001–6,000 of 9,500")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Later text" }));
    await waitFor(() => {
      expect(calls.some((c) => c.path.includes("/text?offset=6000"))).toBe(
        true,
      );
    });
    await userEvent.click(screen.getByRole("button", { name: "Earlier text" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Income" }),
    );
    await waitFor(() => {
      expect(calls.some((c) => c.path.includes("/text?offset=9000"))).toBe(
        true,
      );
    });
  });

  it("keeps the pager when a missing PDF shows stored text", async () => {
    fakeBackend({
      "GET /documents/d1": { ...DETAIL, view_state: "missing" },
      "GET /documents/d1/pages/1": { elements: [], total: 0 },
      "GET /documents/d1/pages/2": { elements: [], total: 0 },
    });
    open("/doc/d1");
    await userEvent.click(
      await screen.findByRole("button", { name: "Next page" }),
    );
    expect(await screen.findByText("Stored text · page 2")).toBeVisible();
  });

  it("shows a new revision when a job finishes, with no refresh", async () => {
    const stored = (text: string) => ({
      elements: [
        { ...row(0), text, locator: { kind: "lines", start: 1, end: 1 } },
      ],
      total: 1,
    });
    let round = 0;
    fakeBackend({
      "GET /documents/d1": () => {
        round += 1;
        const done = round > 1;
        return {
          ...DETAIL,
          document: {
            ...DOC,
            file_kind: "TXT",
            active_revision_id: done ? "r2" : "r1",
          },
          has_live_work: !done,
        };
      },
      "GET /documents/d1/text": () =>
        stored(round > 1 ? "New words" : "Old words"),
    });
    open("/doc/d1");
    expect(await screen.findByText("Old words")).toBeVisible();
    expect(
      await screen.findByText("New words", {}, { timeout: 3000 }),
    ).toBeVisible();
  });

  it("names the model that wrote a card, and why a pending one waits", async () => {
    doc(
      "PDF",
      {},
      {
        card: {
          ...CARD,
          state: "pending",
          written_by: "GPT-6 Luna",
          state_reason:
            "The local model for cards, gemma4:26b, isn't ready in Ollama.",
        },
      },
    );
    open("/doc/d1");
    expect(await screen.findByText("GPT-6 Luna")).toBeVisible();
    expect(
      screen.getByText(
        "The local model for cards, gemma4:26b, isn't ready in Ollama.",
      ),
    ).toBeVisible();
  });
});
