import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, fakeBackend } from "../fakeBackend";
import { installBrowser, open } from "../testApp";
import { THREAD_PAGE } from "../api";

beforeEach(() => {
  installBrowser();
});

function chat(n: number) {
  return {
    id: `t${String(n)}`,
    title: `Chat ${String(n)}`,
    scope: { kind: "library", include_private: false, label: "All" },
    mode: "standard",
    private_tainted: false,
    pinned: false,
    memory_off: false,
    updated_at: 1_700_000_000,
    ask_when_indexed: null,
  };
}

describe("the chat list", () => {
  it("reaches chats beyond the first page", async () => {
    const first = Array.from({ length: THREAD_PAGE }, (_, n) => chat(n));
    fakeBackend({
      "GET /threads": () =>
        calls.at(-1)?.path.includes("offset=50") ? [chat(99)] : first,
    });
    open("/chats");
    await userEvent.click(
      await screen.findByRole("button", { name: "Show older chats" }),
    );
    expect(await screen.findByText("Chat 99")).toBeVisible();
    await waitFor(() => {
      expect(calls.some((c) => c.path.includes("offset=50"))).toBe(true);
      expect(
        screen.queryByRole("button", { name: "Show older chats" }),
      ).toBeNull();
    });
  });

  it("renames, pins and deletes a chat from its row, in both lists", async () => {
    let chats = [chat(1), chat(2)];
    fakeBackend({
      "GET /threads": () => chats,
      "PATCH /threads/t1": () => {
        chats = chats.map((c) =>
          c.id === "t1" ? { ...c, title: "Renamed", pinned: true } : c,
        );
        return chats[0];
      },
      "DELETE /threads/t2": () => {
        chats = chats.filter((c) => c.id !== "t2");
        return null;
      },
    });
    open("/chats");
    const main = await screen.findByRole("main");
    await userEvent.click(
      await within(main).findByRole("button", { name: "Actions for Chat 1" }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const name = screen.getByLabelText("Chat name");
    await userEvent.clear(name);
    await userEvent.type(name, "Renamed");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(screen.getAllByText("Renamed")).toHaveLength(2);
    });
    await userEvent.click(
      within(screen.getByRole("navigation", { name: "SecureFolio" })).getByRole(
        "button",
        { name: "Actions for Chat 2" },
      ),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    await waitFor(() => {
      expect(screen.queryByText("Chat 2")).toBeNull();
    });
    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
  });

  it("offers Unpin on a pinned chat", async () => {
    fakeBackend({ "GET /threads": () => [{ ...chat(3), pinned: true }] });
    open("/chats");
    const main = await screen.findByRole("main");
    await userEvent.click(
      await within(main).findByRole("button", { name: "Actions for Chat 3" }),
    );
    expect(screen.getByRole("menuitem", { name: "Unpin" })).toBeVisible();
  });
});
