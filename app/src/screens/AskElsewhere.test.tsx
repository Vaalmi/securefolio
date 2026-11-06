import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, fakeBackend, ORGANIZE_ESTIMATE } from "../fakeBackend";
import { installBrowser, open, posted } from "../testApp";

beforeEach(() => {
  installBrowser();
});

const THREAD = {
  id: "t1",
  title: "The lease",
  scope: {
    kind: "library",
    include_private: false,
    label: "All · Private excluded",
  },
  mode: "standard",
  private_tainted: false,
  pinned: false,
  memory_off: false,
  updated_at: 1_700_000_000,
  ask_when_indexed: null,
};

describe("other screens that ask", () => {
  it("lists chats", async () => {
    fakeBackend({ "GET /threads": [THREAD] });
    open("/chats");
    expect((await screen.findAllByText("The lease")).length).toBeGreaterThan(0);
  });

  it("asks from a document, and shows what's related", async () => {
    fakeBackend({
      "POST /threads": THREAD,
      "POST /threads/t1/messages": {
        id: "r9",
        thread_id: "t1",
        state: "running",
      },
      "GET /threads/t1": { thread: THREAD, messages: [], active_run: null },
      "GET /documents/d1/related": [
        {
          document_id: "d2",
          title: "Renters policy",
          file_kind: "PDF",
          doc_type: "Policy",
          doc_date: "2025-01-01",
        },
      ],
    });
    open("/doc/d1");
    await userEvent.click(await screen.findByRole("tab", { name: "Related" }));
    expect(await screen.findByText("Renters policy")).toBeVisible();
    await userEvent.click(screen.getByRole("tab", { name: "Ask" }));
    await userEvent.click(
      screen.getByRole("button", { name: /Summarize the key terms/ }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /^Ask Cloud AI/ }),
    );
    await waitFor(() => {
      const made = calls.find(
        (c) => c.path === "/threads" && c.method === "POST",
      );
      expect(made?.body).toMatchObject({
        scope: { kind: "documents", ids: ["d1"] },
      });
    });
  });

  it("edits, pins and deletes a memory", async () => {
    const memory = {
      id: "m1",
      kind: "recap",
      text: "A chat about the lease.",
      sensitivity: "standard",
      source_label: "The lease",
      pinned: false,
      expires_at: 1_800_000_000,
      created_at: 1_700_000_000,
    };
    fakeBackend({
      "GET /memories": [memory],
      "GET /memories/switch": { on: true },
    });
    open("/settings/memory");
    await userEvent.click(
      await screen.findByRole("switch", { name: "Memory" }),
    );
    await waitFor(() => {
      expect(posted("/memories/switch")).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: /^Pin recap/ }));
    await userEvent.click(screen.getByRole("button", { name: /^Edit memory/ }));
    await userEvent.type(screen.getByLabelText("Edit memory"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(
        calls.some(
          (c) =>
            c.method === "PATCH" &&
            c.body !== undefined &&
            "text" in (c.body as object),
        ),
      ).toBe(true);
    });
    await userEvent.click(
      await screen.findByRole("button", { name: /^Delete memory/ }),
    );
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Delete memory",
      }),
    );
    await waitFor(() => {
      expect(calls.some((c) => c.method === "DELETE")).toBe(true);
    });
  });

  it("shows the estimate before a folder is organized in the cloud", async () => {
    const source = {
      id: "s1",
      name: "Home",
      path: "/h",
      builtin: false,
      sensitivity: "standard",
      auto_organize: false,
      mcp_enabled: true,
      status: "watching",
      status_reason: null,
      last_scan_at: 1_700_000_000,
      items: 12,
    };
    fakeBackend({
      "GET /sources": [source],
      "GET /sources/s1/organize-estimate": ORGANIZE_ESTIMATE,
    });
    open("/sources");
    await userEvent.click(
      await screen.findByRole("switch", { name: "Organize Home in the cloud" }),
    );
    const dialog = await screen.findByRole("dialog");
    const turnOn = await within(dialog).findByRole("button", {
      name: /about \$1.25/,
    });
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
    await userEvent.click(turnOn);
    await waitFor(() => {
      const sent = calls.find((c) => c.method === "PATCH");
      expect(sent?.body).toEqual({
        auto_organize: true,
        model: ORGANIZE_ESTIMATE.model,
        input_usd: ORGANIZE_ESTIMATE.input_usd,
        output_usd: ORGANIZE_ESTIMATE.output_usd,
        batched: ORGANIZE_ESTIMATE.batched,
      });
    });
  });

  it("asks about a filtered list", async () => {
    fakeBackend({
      "POST /threads": THREAD,
      "GET /library/ids": ["d1"],
      "GET /threads/t1": { thread: THREAD, messages: [], active_run: null },
    });
    open("/library");
    await userEvent.type(
      await screen.findByLabelText("Filter by name"),
      "lease",
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /Ask about these/ }),
    );
    await waitFor(() => {
      const made = calls.find(
        (c) => c.path === "/threads" && c.method === "POST",
      );
      expect(made?.body).toMatchObject({
        scope: { kind: "search", label: "Search: lease" },
      });
    });
  });
});
