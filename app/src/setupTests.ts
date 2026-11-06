import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { forgetConnection } from "./connection";

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {},
  getDocument: () => ({
    onPassword: undefined,
    promise: Promise.resolve({
      numPages: 3,
      getPage: () =>
        Promise.resolve({
          getViewport: () => ({ width: 600, height: 800 }),
          render: () => ({ cancel: vi.fn(), promise: Promise.resolve() }),
        }),
    }),
    destroy: vi.fn(() => Promise.resolve()),
  }),
}));
vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({ default: "w.js" }));

window.ResizeObserver = class {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
};
Element.prototype.scrollIntoView = vi.fn();

window.ResizeObserver = class {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
};
Element.prototype.scrollIntoView = vi.fn();

afterEach(() => {
  cleanup();
  forgetConnection();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});
