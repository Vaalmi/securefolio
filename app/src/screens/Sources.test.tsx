import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, COLLECTION, DETAIL, fakeBackend } from "../fakeBackend";
import { installBrowser, open, posted } from "../testApp";

beforeEach(() => {
  installBrowser();
});

describe("Sources", () => {
  const source = (extra = {}) => ({
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
    ...extra,
  });

  it("pauses, rescans, switches MCP and changes sensitivity", async () => {
    fakeBackend({ "GET /sources": [source()] });
    open("/sources");
    // A file's first role query runs cold, and on a busy runner takes longer than
    // findBy waits. So this waits for the row by its text, and then asks by role.
    await screen.findByText("/h");
    await userEvent.click(screen.getByRole("button", { name: "Pause Home" }));
    await waitFor(() => {
      expect(posted("/sources/s1/pause")).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Rescan Home" }));
    await userEvent.click(
      screen.getByRole("switch", { name: "MCP access for Home" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "More actions for Home" }),
    );
    await userEvent.click(
      screen.getByRole("menuitem", { name: "Change sensitivity…" }),
    );
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText(/turns off cloud organizing/),
    ).toBeVisible();
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      const sent = calls.find(
        (c) =>
          c.method === "PATCH" &&
          c.body !== undefined &&
          "sensitivity" in (c.body as object),
      );
      expect(sent?.body).toEqual({ sensitivity: "private" });
    });
  });

  it("stops watching after confirming", async () => {
    fakeBackend({ "GET /sources": [source()] });
    open("/sources");
    await userEvent.click(
      await screen.findByRole("button", { name: "More actions for Home" }),
    );
    await userEvent.click(
      screen.getByRole("menuitem", { name: "Stop watching…" }),
    );
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Stop watching",
      }),
    );
    await waitFor(() => {
      expect(
        calls.some((c) => c.method === "DELETE" && c.path === "/sources/s1"),
      ).toBe(true);
    });
  });

  it("adds a folder picked in Finder", async () => {
    window.securefolioChosenPaths = ["/new/folder"];
    fakeBackend({ "GET /sources": [], "POST /sources": source() });
    open("/sources");
    await userEvent.click(
      await screen.findByRole("button", { name: "Add folder" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Add a folder" });
    await userEvent.click(
      within(dialog).getByRole("radio", { name: /Private/ }),
    );
    expect(
      within(dialog).getByText(/aren't offered for Private folders/),
    ).toBeVisible();
    await userEvent.click(
      within(dialog).getByRole("radio", { name: /Standard/ }),
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Add and start indexing" }),
    );
    await waitFor(() => {
      const sent = calls.find(
        (c) => c.method === "POST" && c.path === "/sources",
      );
      expect(sent?.body).toMatchObject({
        path: "/new/folder",
        sensitivity: "standard",
      });
    });
    delete window.securefolioChosenPaths;
  });

  it("shows each state, and leaves the built-in source alone", async () => {
    fakeBackend({
      "GET /sources": [
        source({ id: "a", name: "A", status: "scanning", last_scan_at: null }),
        source({
          id: "b",
          name: "B",
          status: "paused",
          sensitivity: "private",
        }),
        source({
          id: "c",
          name: "C",
          status: "needs_attention",
          status_reason: "3 files not downloaded",
        }),
        source({ id: "d", name: "Copied into library", builtin: true }),
      ],
    });
    open("/sources");
    expect(await screen.findByText("Scanning now")).toBeVisible();
    expect(screen.getByRole("button", { name: "Resume B" })).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "3 files not downloaded" }),
    );
    await waitFor(() => {
      expect(window.location.hash).toContain("tray=skipped");
    });
    expect(
      screen.queryByRole("button", { name: "Pause Copied into library" }),
    ).toBeNull();
  });
});

describe("Collections and Notes", () => {
  it("lists collections and makes a new one", async () => {
    fakeBackend({
      "GET /collections": [
        COLLECTION,
        { ...COLLECTION, id: "c2", name: "Empty", count: 0, titles: [] },
      ],
      "POST /collections": COLLECTION,
    });
    open("/collections");
    expect(await screen.findByText("Roof report")).toBeVisible();
    expect(screen.getByText(/Select documents in the library/)).toBeVisible();
    await userEvent.type(screen.getByLabelText("New collection"), "Papers");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => {
      expect(posted("/collections")).toBe(true);
    });
  });

  it("lists notes and v1's saved searches, each opening Home", async () => {
    fakeBackend({
      "GET /saved-queries": [{ id: "q", text: "pet deposit" }],
    });
    open("/notes");
    expect(await screen.findByText(/No notes yet/)).toBeVisible();
    expect(
      await screen.findByRole("link", { name: /pet deposit/ }),
    ).toHaveAttribute("href", "#/home?q=pet%20deposit");
  });
});

describe("the frame", () => {
  it("opens the types, and the panel at a narrow width", async () => {
    installBrowser(true);
    fakeBackend({ "GET /documents/d1": DETAIL });
    open("/library");
    await userEvent.click(await screen.findByRole("button", { name: "Types" }));
    expect(await screen.findByRole("link", { name: /Contract/ })).toBeVisible();
    await userEvent.click(
      await screen.findByRole("button", { name: "Inspector" }),
    );
    await waitFor(() => {
      expect(window.location.hash).toContain("panel=1");
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "Close inspector" }),
    );
    await waitFor(() => {
      expect(window.location.hash).not.toContain("panel=1");
    });
  });
});
