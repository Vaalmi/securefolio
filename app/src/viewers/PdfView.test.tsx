import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeBackend } from "../fakeBackend";
import { installBrowser } from "../testApp";
import { PdfView } from "./PdfView";

// pdf.js as the tests stand it in, but each page's drawing ends only when the test
// says so, as a real page takes a moment to load and draw.
const { drawing } = vi.hoisted(() => ({
  drawing: [] as { page: number; done: () => void }[],
}));
vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {},
  getDocument: () => ({
    onPassword: undefined,
    promise: Promise.resolve({
      numPages: 3,
      getPage: (page: number) =>
        Promise.resolve({
          getViewport: () => ({ width: 600, height: 800 }),
          render: () => ({
            cancel: vi.fn(),
            promise: new Promise<void>((done) => {
              drawing.push({ page, done });
            }),
          }),
        }),
    }),
    destroy: vi.fn(() => Promise.resolve()),
  }),
}));

const FIGURE = { x0: 0.1, y0: 0.2, x1: 0.3, y1: 0.25 };

beforeEach(() => {
  installBrowser();
  fakeBackend({ "GET /documents/d1/file": new ArrayBuffer(4) });
  drawing.length = 0;
});

function view(page: number, figures = [FIGURE]) {
  return (
    <PdfView
      id="d1"
      page={page}
      zoom={100}
      rects={[]}
      precision={null}
      figures={figures}
      label="Lease"
      onPages={() => undefined}
      onDeclined={() => undefined}
    />
  );
}

async function drawn(page: number): Promise<void> {
  await waitFor(() => {
    expect(drawing.at(-1)?.page).toBe(page);
  });
  act(() => {
    drawing.at(-1)?.done();
  });
}

describe("a PDF page's figures", () => {
  it("show only once their own page is drawn", async () => {
    // Back on page 1 from page 2,
    // page 1's figures waited for nothing, over page 2 still drawn.
    const shown = render(view(1));
    const figure = await screen.findByTestId("pdf-figure");
    expect(figure).not.toBeVisible();
    await drawn(1);
    await waitFor(() => {
      expect(screen.getByTestId("pdf-figure")).toBeVisible();
    });
    shown.rerender(view(2, []));
    await drawn(2);
    shown.rerender(view(1));
    expect(screen.getByTestId("pdf-figure")).not.toBeVisible();
    await drawn(1);
    await waitFor(() => {
      expect(screen.getByTestId("pdf-figure")).toBeVisible();
    });
  });

  it("stay hidden when the page between was left half drawn", async () => {
    // To page 2 and back before page 2 finished, the
    // canvas holds part of page 2 while page 1 loads again.
    const shown = render(view(1));
    await drawn(1);
    await waitFor(() => {
      expect(screen.getByTestId("pdf-figure")).toBeVisible();
    });
    shown.rerender(view(2, []));
    await waitFor(() => {
      expect(drawing.at(-1)?.page).toBe(2);
    });
    shown.rerender(view(1));
    expect(screen.getByTestId("pdf-figure")).not.toBeVisible();
    await drawn(1);
    await waitFor(() => {
      expect(screen.getByTestId("pdf-figure")).toBeVisible();
    });
  });
});
