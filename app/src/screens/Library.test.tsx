import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, DETAIL, DOC, fakeBackend, refusal } from "../fakeBackend";
import { installBrowser, open, posted } from "../testApp";

beforeEach(() => {
  installBrowser();
});

describe("Library", () => {
  const rows = Array.from({ length: 3 }, (_, i) => ({
    ...DOC,
    id: `d${String(i + 1)}`,
    title: `Doc ${String(i + 1)}`,
  }));

  function library(extra = {}) {
    fakeBackend({
      "GET /library": { rows, total: 3, offset: 0 },
      "GET /library/ids": ["d1", "d2", "d3"],
      "GET /documents/d1": DETAIL,
      "GET /documents/d2": { ...DETAIL, document: rows[1] },
      "GET /documents/d3": { ...DETAIL, document: rows[2] },
      ...extra,
    });
  }

  it("selects, adds to a collection and removes after confirming", async () => {
    library();
    open("/library");
    const row = await screen.findByRole("row", { name: /Doc 2/ });
    await userEvent.click(row);
    expect(await screen.findByText("1 selected")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Add to collection" }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: "House" }));
    await waitFor(() => {
      expect(posted("/collections/c1/add")).toBe(true);
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Remove from library" }),
    );
    const dialog = screen.getByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Remove from library" }),
    );
    await waitFor(() => {
      expect(posted("/documents/remove")).toBe(true);
    });
  });

  it("selects every row by id, and sorts through the backend", async () => {
    library();
    open("/library");
    await userEvent.click(
      await screen.findByRole("checkbox", { name: /Select all/ }),
    );
    expect(await screen.findByText("3 selected")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Clear selection" }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^Name/ }));
    await waitFor(() => {
      expect(calls.some((c) => c.path.includes("sort=title"))).toBe(true);
    });
  });

  it("moves through the keyboard grid", async () => {
    library();
    open("/library");
    const body = await screen.findByRole("grid");
    const all = await within(body).findAllByRole("row");
    await userEvent.click(all[1]);
    await userEvent.keyboard("{ArrowDown}");
    await userEvent.keyboard(" ");
    expect(await screen.findByText("2 selected")).toBeVisible();
    await userEvent.keyboard("{Escape}{End}{Home}{PageDown}{PageUp}");
    await userEvent.keyboard("{Control>}a{/Control}");
    expect(await screen.findByText("3 selected")).toBeVisible();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => {
      expect(window.location.hash).toContain("/doc/");
    });
  });

  it("filters through the URL, and clears", async () => {
    library();
    open("/library");
    await userEvent.click(
      await screen.findByRole("button", { name: "Filter by type" }),
    );
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "Contract" }),
    );
    await waitFor(() => {
      expect(window.location.hash).toContain("type=Contract");
    });
    await userEvent.type(screen.getByLabelText("Filter by name"), "lease");
    await waitFor(() => {
      expect(calls.some((c) => c.path.includes("name=lease"))).toBe(true);
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Clear filters" }),
    );
    await waitFor(() => {
      expect(window.location.hash).not.toContain("type=");
    });
  });

  it("says when nothing matches, and when the service failed", async () => {
    fakeBackend({ "GET /library": { rows: [], total: 0, offset: 0 } });
    const first = open("/library?view=starred");
    expect(
      await screen.findByText("No documents match these filters."),
    ).toBeVisible();
    first.unmount();
    fakeBackend({ "GET /library": refusal("Down.", 500) });
    open("/library");
    expect(await screen.findByText("Couldn't load the library")).toBeVisible();
  });

  it("shows a collection's own title, and takes documents out", async () => {
    library();
    open("/collections/c1");
    expect(
      await screen.findByRole("heading", { name: "House", level: 1 }),
    ).toBeVisible();
    const all = await screen.findAllByRole("row");
    await userEvent.click(all[1]);
    await userEvent.click(
      await screen.findByRole("button", { name: "Remove from collection" }),
    );
    await waitFor(() => {
      expect(posted("/collections/c1/remove")).toBe(true);
    });
  });

  it("stars from the inspector", async () => {
    library();
    open("/library");
    await userEvent.click(await screen.findByRole("button", { name: "Star" }));
    await waitFor(() => {
      expect(posted("/documents/d1/star")).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Open" }));
    await waitFor(() => {
      expect(window.location.hash).toContain("/doc/d1");
    });
  });
});
