import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, fakeBackend, ORGANIZE_ESTIMATE, refusal } from "../fakeBackend";
import { installBrowser, open, posted } from "../testApp";

beforeEach(() => {
  installBrowser();
});

const GROUP = {
  state: "installed",
  bytes_done: 2_000_000_000,
  bytes_total: 2_000_000_000,
  reason: null,
};
export const MODELS = {
  search: GROUP,
  page: GROUP,
  local: {
    tag: "gemma4:26b",
    recommended: "gemma4:26b",
    installed: false,
    pull: { state: "not_installed", done: 0, total: 0, reason: null },
  },
  ollama: { running: true, tags: [], refused: null, no_cloud: true },
};

describe("the models step", () => {
  it("starts the download itself, and shows its progress", async () => {
    const waiting = { ...GROUP, state: "not_installed", bytes_done: 0 };
    fakeBackend({
      "GET /models": { ...MODELS, search: waiting },
      "POST /models/download": {},
    });
    open("/setup/models");
    await waitFor(() => {
      expect(posted("/models/download")).toBe(true);
    });
    expect(
      await screen.findByRole("progressbar", { name: "Search and OCR models" }),
    ).toBeVisible();
  });

  it("offers a failed download again, with its reason", async () => {
    const failed = { ...GROUP, state: "failed", reason: "It stopped." };
    fakeBackend({
      "GET /models": { ...MODELS, page: failed },
      "POST /models/download": {},
    });
    open("/setup/models");
    expect(await screen.findByText("It stopped.")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(posted("/models/download")).toBe(true);
  });

  it("shows a local download going, and pulls on request", async () => {
    fakeBackend({
      "GET /models": MODELS,
      "POST /models/local/download": {},
    });
    open("/setup/models");
    await userEvent.click(
      await screen.findByRole("button", { name: "Download local model" }),
    );
    expect(posted("/models/local/download")).toBe(true);
    fakeBackend({
      "GET /models": {
        ...MODELS,
        local: {
          ...MODELS.local,
          pull: { state: "downloading", done: 1, total: 2, reason: null },
        },
      },
    });
  });

  it.each([
    [
      { running: false, tags: [], refused: null, no_cloud: true },
      /Ollama isn't running/,
    ],
    [
      {
        running: false,
        tags: [],
        refused: "Must be on this Mac.",
        no_cloud: true,
      },
      /Must be on this Mac/,
    ],
    [
      { running: true, tags: [], refused: null, no_cloud: false },
      /OLLAMA_NO_CLOUD/,
    ],
  ])("says what's wrong with Ollama", async (ollama, words) => {
    fakeBackend({ "GET /models": { ...MODELS, ollama } });
    open("/setup/models");
    expect(await screen.findByText(words)).toBeVisible();
  });

  it("goes back and on", async () => {
    fakeBackend({ "GET /models": MODELS });
    open("/setup/models");
    await userEvent.click(await screen.findByRole("button", { name: "Back" }));
    expect(
      await screen.findByRole("heading", { name: "Connect a cloud model" }),
    ).toBeVisible();
  });
});

describe("the folders step", () => {
  it("adds what was chosen, private or not, then starts", async () => {
    window.securefolioChosenPaths = ["/h/Taxes"];
    fakeBackend({ "POST /sources": { id: "s1" }, "GET /jobs/counts": {} });
    open("/setup/folders");
    await userEvent.click(screen.getByRole("button", { name: "Add folder…" }));
    await screen.findByText("/h/Taxes");
    await userEvent.click(screen.getByRole("radio", { name: /Private/ }));
    await userEvent.click(
      screen.getByRole("button", { name: "Start indexing" }),
    );
    await waitFor(() => {
      const sent = calls.find(
        (c) => c.path === "/sources" && c.method === "POST",
      );
      expect(sent?.body).toMatchObject({
        path: "/h/Taxes",
        sensitivity: "private",
      });
    });
    expect(
      await screen.findByRole("heading", { name: "Indexing has started" }),
    ).toBeVisible();
    window.securefolioChosenPaths = undefined;
  });

  it("goes back", async () => {
    fakeBackend({ "GET /models": MODELS });
    open("/setup/folders");
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(
      await screen.findByRole("heading", { name: /search models/ }),
    ).toBeVisible();
  });
});

describe("the ready step", () => {
  const COUNTS = { active: 0, done: 4, total: 9 };

  it("imports v1 only from what was chosen, and finishes", async () => {
    fakeBackend({
      "GET /jobs/counts": COUNTS,
      "POST /import/v1": { at: 1, notes: 2, sessions: 1, saved_queries: 3 },
      "PATCH /settings": {},
    });
    open("/setup/ready");
    await userEvent.click(
      screen.getByRole("button", { name: "Choose metadata.db…" }),
    );
    expect(posted("/import/v1")).toBe(false);
    window.securefolioChosenPaths = ["/old/metadata.db"];
    await userEvent.click(
      screen.getByRole("button", { name: "Choose metadata.db…" }),
    );
    expect(await screen.findByText(/Imported 2 notes/)).toBeVisible();
    window.securefolioChosenPaths = undefined;
    await userEvent.click(
      screen.getByRole("button", { name: "Open SecureFolio" }),
    );
    await waitFor(() => {
      const sent = calls.find(
        (c) => c.path === "/settings" && c.method === "PATCH",
      );
      expect(sent?.body).toEqual({ setup_done: true });
    });
  });

  it("names why a v1 file wasn't imported, and offers Claude", async () => {
    window.securefolioChosenPaths = ["/old/other.db"];
    fakeBackend({
      "GET /jobs/counts": COUNTS,
      "POST /import/v1": refusal("The table notes is missing."),
    });
    open("/setup/ready");
    await userEvent.click(
      screen.getByRole("button", { name: "Choose metadata.db…" }),
    );
    expect(
      await screen.findByText("The table notes is missing."),
    ).toBeVisible();
    window.securefolioChosenPaths = undefined;
    fireEvent.click(
      screen.getByRole("button", { name: "Install for Claude Desktop" }),
    );
    expect(await screen.findByText(/Confirm there to install/)).toBeVisible();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.resolve() },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Copy Claude Code command" }),
    );
    expect(await screen.findByText(/Copied the Claude Code/)).toBeVisible();
  });
});

describe("organizing, on the ready step", () => {
  it("is offered only for a Standard folder, with its estimate", async () => {
    fakeBackend({
      "GET /jobs/counts": { active: 0, done: 1, total: 1 },
      "GET /sources": [
        {
          id: "s1",
          name: "Home",
          path: "/h",
          sensitivity: "standard",
          builtin: false,
          auto_organize: false,
          items: 3,
        },
        {
          id: "s2",
          name: "Taxes",
          path: "/t",
          sensitivity: "private",
          builtin: false,
          auto_organize: false,
          items: 3,
        },
      ],
      "GET /sources/s1/organize-estimate": {
        ...ORGANIZE_ESTIMATE,
        backfill_usd: 0.02,
        per_document_usd: 0.001,
      },
    });
    open("/setup/ready");
    await userEvent.click(
      await screen.findByRole("button", { name: /See what it sends/ }),
    );
    expect(await screen.findByText("$0.02")).toBeVisible();
    expect(screen.queryByText(/at the batch price/)).toBeNull();
    expect(
      screen.getAllByRole("button", { name: /See what it sends/ }),
    ).toHaveLength(1);
  });
});

describe("the default sensitivity", () => {
  it("is what a new folder starts with", async () => {
    window.securefolioChosenPaths = ["/h/New"];
    fakeBackend({
      "GET /settings": { setup_done: true, default_sensitivity: "private" },
    });
    open("/setup/folders");
    await screen.findByRole("heading", { name: "Choose folders to watch" });
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/settings")).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Add folder…" }));
    expect(await screen.findByRole("radio", { name: /Private/ })).toBeChecked();
    window.securefolioChosenPaths = undefined;
  });
});

describe("before the default sensitivity is known", () => {
  it("offers no folder, and says if it can't find out", async () => {
    fakeBackend({ "GET /settings": refusal("Settings can't be read.", 500) });
    open("/setup/folders");
    expect(await screen.findByText("Settings can't be read.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Add folder…" })).toBeDisabled();
  });
});

describe("the app before first run is done", () => {
  it("goes to the key step", async () => {
    fakeBackend({ "GET /settings": { setup_done: false } });
    open("/home");
    expect(
      await screen.findByRole("heading", { name: "Connect a cloud model" }),
    ).toBeVisible();
  });
});
