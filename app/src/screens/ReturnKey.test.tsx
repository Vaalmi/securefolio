import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import {
  calls,
  CLOUD_ROUTE,
  DOC,
  fakeBackend,
  LOCAL_ROUTE,
} from "../fakeBackend";
import { installBrowser, open } from "../testApp";
import { ANSWER, chat } from "./askFixtures";

// The Return key: on Home and in quick search, Return asks and Command-Return opens;
// in a question box, Return sends.

beforeEach(() => {
  installBrowser();
});

const PASSAGE = {
  id: "p1",
  element_id: "e1",
  snippet: "The monthly rent is due.",
  locator: { kind: "pdf", page: 2, rects: [], precision: "page" },
  label: "Page 2",
  document: DOC,
};

function home(route: object = CLOUD_ROUTE) {
  fakeBackend({
    "GET /ask/route": route,
    "GET /search": {
      documents: [DOC],
      passages: [PASSAGE],
      hidden_private: 0,
      semantic: true,
    },
  });
  open("/home");
}

async function typed(text: string): Promise<HTMLElement> {
  const box = await screen.findByRole("combobox");
  await userEvent.type(box, text);
  await screen.findAllByRole("option");
  await screen.findByRole("button", { name: /^Ask Cloud AI|^Ask Local AI/ });
  return box;
}

// Asking goes to a new chat and sends the question there: the question sent.
async function sentQuestion(): Promise<unknown> {
  return waitFor(() => {
    const sent = calls.find(
      (c) => c.method === "POST" && c.path.endsWith("/messages"),
    );
    if (!sent) throw new Error("nothing sent yet");
    return (sent.body as { text?: string }).text;
  });
}

function rows(): HTMLElement[] {
  return within(screen.getByRole("listbox")).getAllByRole("option");
}

describe("on Home", () => {
  it("Return asks", async () => {
    home();
    await typed("rent");
    await userEvent.keyboard("{Enter}");
    expect(await sentQuestion()).toBe("rent");
  });

  it.each([["{Meta>}{Enter}{/Meta}"], ["{Control>}{Enter}{/Control}"]])(
    "%s opens the first match",
    async (keys) => {
      home();
      await typed("rent");
      await userEvent.keyboard(keys);
      await waitFor(() => {
        expect(window.location.hash).toBe("#/doc/d1");
      });
    },
  );

  it("after the arrows, Return opens the result moved to; typing again asks", async () => {
    home();
    const box = await typed("rent");
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    expect(rows()[1]).toHaveAttribute("aria-selected", "true");
    await userEvent.type(box, "s");
    await screen.findAllByRole("option");
    expect(rows().map((r) => r.getAttribute("aria-selected"))).toEqual([
      "false",
      "false",
    ]);
    await userEvent.keyboard("{Enter}");
    expect(await sentQuestion()).toBe("rents");
  });

  it("after the arrows, Return opens the result moved to", async () => {
    home();
    await typed("rent");
    await userEvent.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    await waitFor(() => {
      expect(window.location.hash).toBe("#/doc/d1?el=e1");
    });
  });

  it("hovering doesn't change what either key opens", async () => {
    home();
    await typed("rent");
    await userEvent.hover(rows()[1]);
    expect(rows()[1]).toHaveAttribute("aria-selected", "false");
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    await waitFor(() => {
      expect(window.location.hash).toBe("#/doc/d1");
    });
  });

  it("keys that pick an input-method candidate do nothing", async () => {
    home();
    const box = await typed("rent");
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    fireEvent.keyDown(box, { key: "Enter", metaKey: true, isComposing: true });
    fireEvent.keyDown(box, { key: "ArrowDown", isComposing: true });
    expect(window.location.hash).toBe("#/home");
    expect(rows()[0]).toHaveAttribute("aria-selected", "false");
  });
});

describe("Return's edge cases", () => {
  it("WebKit's Return that confirms an input-method candidate does nothing", async () => {
    // With isComposing false, keyCode 229, as WebKit can send it.
    home();
    const box = await typed("rent");
    fireEvent.keyDown(box, { key: "Enter", keyCode: 229 });
    fireEvent.keyDown(box, { key: "Enter", metaKey: true, keyCode: 229 });
    expect(window.location.hash).toBe("#/home");
    expect(
      calls.some((c) => c.method === "POST" && c.path.startsWith("/threads")),
    ).toBe(false);
  });

  it("the follow-up box doesn't send on it either", async () => {
    chat(ANSWER, {
      "POST /threads/t1/messages": {
        id: "r2",
        thread_id: "t1",
        state: "running",
      },
    });
    open("/ask/t1");
    const box = await screen.findByPlaceholderText("Ask a follow-up");
    await screen.findByRole("button", { name: /^Ask Cloud AI/ });
    await userEvent.type(box, "And?");
    fireEvent.keyDown(box, { key: "Enter", keyCode: 229 });
    expect(box).toHaveValue("And?");
    expect(calls.some((c) => c.path === "/threads/t1/messages")).toBe(false);
  });

  it("another scope with fewer results puts Return back to asking", async () => {
    // The arrows on the second of two results, then a scope with one.
    fakeBackend({
      "GET /ask/route": CLOUD_ROUTE,
      "GET /search": () => {
        const wide = calls.at(-1)?.path.includes("include_private=true");
        return {
          documents: [DOC],
          passages: wide ? [] : [PASSAGE],
          hidden_private: 0,
          semantic: true,
        };
      },
    });
    open("/home");
    await typed("rent");
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    expect(rows()[1]).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("button", { name: /^Scope:/ }));
    await userEvent.click(
      screen.getByRole("menuitemradio", { name: /Private included/ }),
    );
    await waitFor(() => {
      expect(rows()).toHaveLength(1);
    });
    expect(rows()[0]).toHaveAttribute("aria-selected", "false");
    expect(document.querySelector(".results-foot")).toHaveTextContent(
      "⌘↵ open first",
    );
    expect(document.querySelector(".results-foot")).not.toHaveTextContent(
      "↵ open selected",
    );
    screen.getByRole("combobox").focus();
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    await waitFor(() => {
      expect(window.location.hash).toBe("#/doc/d1");
    });
  });
});

describe("the hints", () => {
  it("mark the first match ⌘↵ without selecting it, and the moved-to row ↵", async () => {
    home();
    await typed("rent");
    const [first, second] = rows();
    expect(first).toHaveAttribute("aria-selected", "false");
    expect(first).toHaveClass("first");
    expect(
      within(first).getByTitle("Command-Return opens this"),
    ).toHaveTextContent("⌘↵");
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    expect(within(second).getByTitle("Return opens this")).toHaveTextContent(
      "↵",
    );
    expect(within(first).queryByTitle(/opens this/)).toBeNull();
  });

  it("say which key does what, beside the button and under the list", async () => {
    home();
    await typed("rent");
    const button = screen.getByRole("button", { name: /^Ask Cloud AI/ });
    const hint = document.getElementById(
      button
        .closest(".composer")
        ?.querySelector("input")
        ?.getAttribute("aria-describedby") ?? "",
    );
    expect(hint).toHaveTextContent("↵ ask · ⌘↵ open first result");
    const foot = document.querySelector(".results-foot");
    expect(foot).toHaveTextContent("↵ ask Cloud AI");
    expect(foot).toHaveTextContent("⌘↵ open first");
    expect(button).toHaveAttribute("aria-keyshortcuts", "Enter");
    expect(button).toHaveTextContent("↵");
    await userEvent.keyboard("{ArrowDown}");
    expect(hint).toHaveTextContent("↵ open this result · type to ask");
    expect(foot).toHaveTextContent("⌘↵ open selected");
    expect(button).not.toHaveAttribute("aria-keyshortcuts");
    expect(button).not.toHaveTextContent("↵");
  });

  it("drop Return from the button while answering is blocked", async () => {
    home({ ...LOCAL_ROUTE, blocked: "Ollama isn't running on this Mac." });
    const box = await screen.findByRole("combobox");
    await userEvent.type(box, "rent");
    const button = await screen.findByRole("button", { name: /^Ask Local AI/ });
    expect(button).not.toHaveAttribute("aria-keyshortcuts");
    expect(button).not.toHaveTextContent("↵");
    expect(document.querySelector(".results-foot")).not.toHaveTextContent(
      "↵ ask",
    );
  });

  it("asks for the route again when its window comes back", async () => {
    // An open window kept a Cloud route after the key was removed
    // in another.
    let route: object = CLOUD_ROUTE;
    fakeBackend({
      "GET /ask/route": () => route,
      "GET /search": {
        documents: [],
        passages: [],
        hidden_private: 0,
        semantic: true,
      },
    });
    open("/home");
    const box = await screen.findByRole("combobox");
    await userEvent.type(box, "rent");
    await screen.findByRole("button", { name: /^Ask Cloud AI/ });
    route = LOCAL_ROUTE; // the key removed in another window
    fireEvent.focus(window);
    expect(
      await screen.findByRole("button", { name: /^Ask Local AI/ }),
    ).toBeVisible();
  });

  it("opens the box once a blocked route can answer, without leaving", async () => {
    // A blocked route was asked only once.
    let route: object = { ...LOCAL_ROUTE, blocked: "Ollama isn't running." };
    fakeBackend({
      "GET /ask/route": () => route,
      "GET /search": {
        documents: [],
        passages: [],
        hidden_private: 0,
        semantic: true,
      },
    });
    open("/home");
    const box = await screen.findByRole("combobox");
    await userEvent.type(box, "rent");
    const button = await screen.findByRole("button", { name: /^Ask Local AI/ });
    expect(button).toBeDisabled();
    route = LOCAL_ROUTE; // Ollama started
    await waitFor(
      () => {
        expect(
          screen.getByRole("button", { name: /^Ask Local AI/ }),
        ).toBeEnabled();
      },
      { timeout: 4000 },
    );
  });
});

describe("in a question box", () => {
  it("the follow-up box sends on Return, and Shift-Return starts a new line", async () => {
    chat(ANSWER, {
      "POST /threads/t1/messages": {
        id: "r2",
        thread_id: "t1",
        state: "running",
      },
    });
    open("/ask/t1");
    const box = await screen.findByPlaceholderText("Ask a follow-up");
    await screen.findByRole("button", { name: /^Ask Cloud AI/ });
    await userEvent.type(box, "And{Shift>}{Enter}{/Shift}the deposit?");
    expect(box).toHaveValue("And\nthe deposit?");
    expect(calls.some((c) => c.path === "/threads/t1/messages")).toBe(false);
    await userEvent.keyboard("{Enter}");
    await waitFor(() => {
      const sent = calls.find((c) => c.path === "/threads/t1/messages");
      expect(sent?.body).toMatchObject({ text: "And\nthe deposit?" });
    });
    expect(
      document.getElementById(box.getAttribute("aria-describedby") ?? ""),
    ).toHaveTextContent("↵ send · ⇧↵ new line");
  });

  it("Ask this document sends on Return", async () => {
    fakeBackend({});
    open("/doc/d1");
    await userEvent.click(await screen.findByRole("tab", { name: "Ask" }));
    const box = screen.getByLabelText("Ask this document");
    await screen.findByRole("button", { name: /^Ask Cloud AI/ });
    await userEvent.type(box, "Who signs it?{Enter}");
    expect(await sentQuestion()).toBe("Who signs it?");
  });
});
