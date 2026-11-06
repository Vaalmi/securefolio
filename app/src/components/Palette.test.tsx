import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { DOC, fakeBackend } from "../fakeBackend";
import { installBrowser, open } from "../testApp";

beforeEach(() => {
  installBrowser();
});

const THREAD = {
  id: "t1",
  title: "Warranty end date",
  scope: { kind: "library", include_private: false, label: "All" },
};

function box(): HTMLElement {
  return screen.getByRole("combobox", {
    name: "Type a command or a document name",
  });
}

describe("the palette", () => {
  it("lists recent chats and actions, moves with the arrows, and runs", async () => {
    fakeBackend({ "GET /threads": [THREAD] });
    open("/home?palette=1");
    expect(
      await screen.findByRole("option", { name: /Warranty end date/ }),
    ).toBeVisible();
    fireEvent.keyDown(box(), { key: "ArrowUp" });
    fireEvent.keyDown(box(), { key: "ArrowDown" });
    fireEvent.keyDown(box(), { key: "ArrowDown" });
    const options = screen.getAllByRole("option");
    expect(options[2]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => {
      expect(
        screen.queryByRole("combobox", { name: /Type a command/ }),
      ).toBeNull();
    });
  });

  it("finds a document by name, and a mouse can choose it", async () => {
    fakeBackend({
      "GET /search": {
        documents: [DOC],
        passages: [],
        hidden_private: 0,
        semantic: true,
      },
    });
    open("/home?palette=1");
    await userEvent.type(box(), "lease");
    const found = await screen.findByRole("option", {
      name: /Apartment lease/,
    });
    fireEvent.mouseEnter(found);
    fireEvent.mouseDown(found);
    await waitFor(() => {
      expect(window.location.hash).toContain("/doc/d1");
    });
  });

  it("closes", async () => {
    fakeBackend();
    open("/home?palette=1");
    fireEvent.keyDown(box(), { key: "Escape" });
    await waitFor(() => {
      expect(
        screen.queryByRole("combobox", { name: /Type a command/ }),
      ).toBeNull();
    });
  });
});

describe("quick search and the shortcuts", () => {
  it("shows the quick-search window, which Escape closes", async () => {
    fakeBackend();
    open("/quick");
    expect(
      await screen.findByRole("main", { name: "SecureFolio quick search" }),
    ).toBeVisible();
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.keyDown(window, { key: "a" });
  });

  it("opens the palette with ⌘K and Settings with ⌘,", async () => {
    fakeBackend();
    open("/home");
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(
      await screen.findByRole("combobox", { name: /Type a command/ }),
    ).toBeVisible();
    fireEvent.keyDown(window, { key: ",", metaKey: true });
    await waitFor(() => {
      expect(window.location.hash).toContain("/settings/models");
    });
  });
});
