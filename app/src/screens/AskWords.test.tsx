import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { askLabel } from "../askRoute";
import { calls, CLOUD_ROUTE, fakeBackend, LOCAL_ROUTE } from "../fakeBackend";
import { installBrowser, open } from "../testApp";
import { ANSWER, chat, THREAD } from "./askFixtures";

// Ask says Cloud AI or Local AI: the button in every box, from the backend's route,
// each answer, the cards and the notes.

beforeEach(() => {
  installBrowser();
});

const GPT = {
  ...CLOUD_ROUTE,
  model: "openai:gpt-6.1-sol",
  name: "GPT-6.1 Sol",
  provider: "openai",
};
const NO_KEY = {
  ...LOCAL_ROUTE,
  reason:
    "GPT-6.1 Sol needs an OpenAI key (or its Project ID). Add it in Settings, or choose another model.",
  needs_key: true,
};
const BLOCKED = {
  ...LOCAL_ROUTE,
  blocked: "Ollama isn't running on this Mac, so Local AI can't answer.",
};

function routeAsks(): string[] {
  return calls
    .filter((c) => c.path.startsWith("/ask/route"))
    .map((c) => c.path);
}

describe("the Ask button", () => {
  it("is named by one function: Cloud AI, Deep, or Local AI", () => {
    expect(askLabel(CLOUD_ROUTE, "standard")).toBe("Ask Cloud AI");
    expect(askLabel(CLOUD_ROUTE, "deep")).toBe("Ask Cloud AI · Deep");
    expect(askLabel(LOCAL_ROUTE, "deep")).toBe("Ask Local AI");
  });

  it("on Home, follows the route for the scope chosen", async () => {
    fakeBackend({ "GET /ask/route": LOCAL_ROUTE });
    open("/home");
    expect(
      await screen.findByRole("button", { name: /^Ask Local AI/ }),
    ).toBeVisible();
    expect(routeAsks()).toContain(
      "/ask/route?mode=standard&include_private=false",
    );
  });

  it("on a document, asks the route for that document", async () => {
    fakeBackend({ "GET /ask/route": GPT });
    open("/doc/d1");
    await userEvent.click(await screen.findByRole("tab", { name: "Ask" }));
    const button = await screen.findByRole("button", { name: /^Ask Cloud AI/ });
    expect(button).toHaveAttribute("title", "GPT-6.1 Sol");
    expect(routeAsks()).toContain("/ask/route?mode=standard&documents=d1");
  });

  it("in a chat, asks the route for that thread and its scope", async () => {
    chat(ANSWER, {
      "GET /ask/route": CLOUD_ROUTE,
      "GET /threads/t1": {
        thread: {
          ...THREAD,
          scope: { kind: "collection", id: "c1", label: "House" },
        },
        messages: [],
        active_run: null,
      },
    });
    open("/ask/t1");
    expect(
      await screen.findByRole("button", { name: /^Ask Cloud AI/ }),
    ).toBeVisible();
    await waitFor(() => {
      expect(routeAsks()).toContain(
        "/ask/route?mode=standard&thread=t1&collection=c1",
      );
    });
  });

  it("is disabled where nothing can answer, saying why", async () => {
    fakeBackend({ "GET /ask/route": BLOCKED });
    open("/home");
    const box = await screen.findByRole("combobox");
    await userEvent.type(box, "When is rent due?");
    expect(
      screen.getByRole("button", { name: /^Ask Local AI/ }),
    ).toBeDisabled();
    expect(screen.getByText(/Ollama isn't running on this Mac/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Local models" })).toHaveAttribute(
      "href",
      "#/settings/local",
    );
    await userEvent.keyboard("{Enter}");
    expect(window.location.hash).toBe("#/home");
  });

  it("says Local AI answers for want of a key, with the way to Keys and models", async () => {
    fakeBackend({ "GET /ask/route": NO_KEY });
    open("/home");
    expect(
      await screen.findByText(
        /Local AI answers on this Mac\. GPT-6\.1 Sol needs an OpenAI key/,
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Keys and models" }),
    ).toHaveAttribute("href", "#/settings/models");
  });

  it("names each mode's model in its hint", async () => {
    fakeBackend({
      "GET /ask/route": GPT,
    });
    open("/home");
    const deep = await screen.findByRole("radio", { name: "Deep" });
    await waitFor(() => {
      expect(deep).toHaveAttribute(
        "title",
        "Cloud AI, GPT-6.1 Sol, slower and costs more",
      );
    });
    expect(screen.getByRole("radio", { name: "Standard" })).toHaveAttribute(
      "title",
      "Cloud AI, GPT-6.1 Sol",
    );
  });
});

describe("each answer", () => {
  it("reads Cloud AI and its model", async () => {
    chat({ ...ANSWER, model: "openai:gpt-6.1-sol", model_name: "GPT-6.1 Sol" });
    open("/ask/t1");
    expect(await screen.findByText(/Cloud AI · GPT-6\.1 Sol/)).toBeVisible();
  });

  it("reads Local AI and its tag", async () => {
    chat({
      ...ANSWER,
      model: "gemma4:26b",
      model_name: "gemma4:26b",
      local: true,
    });
    open("/ask/t1");
    expect(await screen.findByText("Local AI · gemma4:26b")).toBeVisible();
  });

  it("doesn't call a local answer free when its helpers went to the cloud", async () => {
    // It said "no cloud cost".
    chat({
      ...ANSWER,
      model: "gemma4:26b",
      model_name: "gemma4:26b",
      local: true,
      helper_local: false,
      cost_usd: 0,
    });
    open("/ask/t1");
    await screen.findByText("Local AI · gemma4:26b");
    expect(screen.getByText("· helper steps by Cloud AI")).toBeVisible();
    expect(screen.queryByText(/no cloud cost/)).toBeNull();
  });

  it("says a local answer with nothing in the cloud cost nothing", async () => {
    chat({
      ...ANSWER,
      model: "gemma4:26b",
      model_name: "gemma4:26b",
      local: true,
      helper_local: true,
      cost_usd: 0,
    });
    open("/ask/t1");
    expect(await screen.findByText("· no cloud cost")).toBeVisible();
  });

  it("claims nothing about an older local answer's helpers", async () => {
    // Older answers don't say where their helpers ran.
    chat({
      ...ANSWER,
      model: "gemma4:26b",
      model_name: "gemma4:26b",
      local: true,
      cost_usd: 0,
    });
    open("/ask/t1");
    await screen.findByText("Local AI · gemma4:26b");
    expect(screen.queryByText(/no cloud cost/)).toBeNull();
    expect(screen.queryByText(/helper steps by Cloud AI/)).toBeNull();
  });

  it("offers Re-run in Deep only when Deep would go to the cloud", async () => {
    chat(ANSWER, { "GET /ask/route": { ...CLOUD_ROUTE, deep_cloud: false } });
    open("/ask/t1");
    await screen.findByText(/Cloud AI · Claude Sonnet 5\.5/);
    await waitFor(() => {
      expect(routeAsks().length).toBeGreaterThan(0);
    });
    expect(screen.queryByRole("button", { name: "Re-run in Deep" })).toBeNull();
  });
});

describe("the chat's cards and notes", () => {
  const paused = (card: object) => ({
    "GET /threads/t1": {
      thread: THREAD,
      messages: [],
      active_run: {
        id: "r1",
        thread_id: "t1",
        state: "paused",
        card,
        error: null,
      },
    },
  });

  it("the Private card says Cloud AI and Local AI", async () => {
    chat(ANSWER, {
      ...paused({
        kind: "private_in_scope",
        id: "c1",
        documents: [{ id: "d1", title: "Federal return" }],
      }),
    });
    open("/ask/t1");
    const card = (await screen.findByText(/Federal return/)).closest(
      ".consent",
    );
    if (!(card instanceof HTMLElement)) throw new Error("no card");
    expect(card).toHaveTextContent(
      "Private documents never go to any cloud model.",
    );
    expect(card).toHaveTextContent("Local AI answers from now on");
    expect(card).toHaveTextContent(
      "Cloud AI keeps answering from Standard documents only.",
    );
    expect(card.textContent).not.toMatch(/Claude/);
  });

  it("the Deep card names Cloud AI and the model it priced", async () => {
    chat(ANSWER, {
      ...paused({
        kind: "deep_cost",
        id: "c2",
        question: "All of it?",
        model: "openai:gpt-6-astra",
        model_name: "GPT-6 Astra",
        estimate_usd: 0.74,
        documents: 42,
        spent_usd: 4.82,
        cap_usd: 25,
      }),
    });
    open("/ask/t1");
    expect(
      await screen.findByText(
        /Cloud AI \(GPT-6 Astra\) will read 42 documents/,
      ),
    ).toBeVisible();
  });

  it("the note under the box names Cloud AI and its model, or Local AI", async () => {
    chat(ANSWER, { "GET /ask/route": GPT });
    open("/ask/t1");
    expect(
      await screen.findByText(
        /Cloud AI \(GPT-6\.1 Sol\) gets this question, recent turns/,
      ),
    ).toBeVisible();
  });

  it("the note says Local AI answers on this Mac in a local chat", async () => {
    chat(ANSWER, { "GET /ask/route": LOCAL_ROUTE }, { private_tainted: true });
    open("/ask/t1");
    const note = await screen.findByText(
      /Nothing in this chat leaves your Mac/,
    );
    expect(note).toHaveTextContent("Local AI answers on this Mac.");
    expect(
      within(document.body).queryByText(/Claude gets this question/),
    ).toBeNull();
  });

  it("says what still leaves the Mac when Local AI answers and the helper is in the cloud", async () => {
    // The answer stays here, its helper steps don't.
    chat(ANSWER, {
      "GET /ask/route": {
        ...LOCAL_ROUTE,
        helper_local: false,
        helper_name: "Claude Haiku 5.5",
      },
    });
    open("/ask/t1");
    const note = await screen.findByText(/runs its helper steps/);
    expect(note).toHaveTextContent(
      "Local AI answers on this Mac. Cloud AI (Claude Haiku 5.5) runs its helper steps, so it gets this question and recent turns.",
    );
    expect(
      screen.queryByText(/Nothing in this chat leaves your Mac/),
    ).toBeNull();
  });
});

// A route that answers only when told to, for asking before it has come.
function heldRoute(route: object) {
  let answer: (() => void) | undefined;
  return {
    handler: () =>
      new Promise<Response>((resolve) => {
        answer = () => {
          resolve(new Response(JSON.stringify(route)));
        };
      }),
    answer: () => answer?.(),
  };
}

describe("asking before the route has come", () => {
  it("on Home, the button waits as plain Ask, and nothing is asked", async () => {
    // A route that will say blocked, not yet answered.
    const held = heldRoute(BLOCKED);
    fakeBackend({ "GET /ask/route": held.handler });
    open("/home");
    const box = await screen.findByRole("combobox");
    await userEvent.type(box, "When is rent due?");
    const button = screen.getByRole("button", { name: /^Ask/ });
    expect(button).toHaveTextContent(/^Ask$/);
    expect(button).toBeDisabled();
    await userEvent.keyboard("{Enter}");
    expect(window.location.hash).toBe("#/home");
  });

  it("in a chat, keeps the draft and sends nothing", async () => {
    const held = heldRoute(CLOUD_ROUTE);
    chat(ANSWER, { "GET /ask/route": held.handler });
    open("/ask/t1");
    const box = await screen.findByPlaceholderText("Ask a follow-up");
    await userEvent.type(box, "And the deposit?");
    await userEvent.keyboard("{Enter}");
    expect(box).toHaveValue("And the deposit?");
    expect(
      calls.some(
        (c) => c.method === "POST" && c.path === "/threads/t1/messages",
      ),
    ).toBe(false);
  });

  it("doesn't count the last scope's route while the new one comes", async () => {
    const now: { held?: ReturnType<typeof heldRoute> } = {};
    fakeBackend({
      "GET /ask/route": () => now.held?.handler() ?? CLOUD_ROUTE,
    });
    open("/home");
    const box = await screen.findByRole("combobox");
    await userEvent.type(box, "When is rent due?");
    expect(
      await screen.findByRole("button", { name: /^Ask Cloud AI/ }),
    ).toBeEnabled();
    const held = heldRoute(LOCAL_ROUTE);
    now.held = held;
    await userEvent.click(screen.getByRole("radio", { name: "Deep" }));
    expect(screen.getByRole("button", { name: /^Ask/ })).toBeDisabled();
    held.answer();
    expect(
      await screen.findByRole("button", { name: /^Ask Local AI/ }),
    ).toBeEnabled();
  });

  it("says so when it can't find out who would answer", async () => {
    fakeBackend({
      "GET /ask/route": () =>
        new Response(JSON.stringify({ detail: "No." }), { status: 500 }),
    });
    open("/home");
    expect(
      await screen.findByText(/SecureFolio couldn't tell who would answer/),
    ).toBeVisible();
  });
});
