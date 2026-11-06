import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, fakeBackend, refusal } from "../fakeBackend";
import { installBrowser, open } from "../testApp";

// First run: the key step with any one of three providers, its optional ID and the
// needs-ID state, or no key; and Ready saying what answers without one.

beforeEach(() => {
  installBrowser();
});

const GROUP = {
  state: "installed",
  bytes_done: 9,
  bytes_total: 9,
  reason: null,
};
const MODELS = {
  search: GROUP,
  page: GROUP,
  jobs: {},
  installed: { local: [], remote: [] },
  memory: { gb: 0, about: false },
  mac_memory_gb: 48,
  local: {
    tag: "gemma4:26b",
    recommended: "gemma4:26b",
    installed: false,
    pull: { state: "not_installed", done: 0, total: 0, reason: null },
  },
  ollama: { running: true, tags: [], refused: null, no_cloud: true },
};

function tested(provider: string) {
  return calls.filter(
    (c) => c.method === "POST" && c.path === `/settings/keys/${provider}/test`,
  );
}

const NEEDS_ID = refusal(
  "This key isn't tied to one project. Add its Project ID, then Test again.",
  422,
);

describe("the key step", () => {
  it("tests an Anthropic key, says it works, and goes on", async () => {
    fakeBackend({
      "POST /settings/keys/anthropic/test": {},
      "GET /models": MODELS,
    });
    open("/setup/key");
    const go = screen.getByRole("button", { name: "Continue" });
    expect(go).toBeDisabled();
    await userEvent.type(
      screen.getByLabelText("Anthropic API key"),
      "sk-ant-x",
    );
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/Key works/)).toBeVisible();
    expect(tested("anthropic").at(-1)?.body).toEqual({
      key: "sk-ant-x",
      scope_id: "",
    });
    await userEvent.click(go);
    expect(
      await screen.findByRole("heading", { name: /search models/ }),
    ).toBeVisible();
  });

  it("offers each provider with its own ID, by arrow keys", async () => {
    fakeBackend({});
    open("/setup/key");
    const anthropic = screen.getByRole("radio", { name: "Anthropic" });
    expect(anthropic).toHaveAttribute("aria-checked", "true");
    expect(screen.getByLabelText("Workspace ID (optional)")).toBeVisible();
    anthropic.focus();
    await userEvent.keyboard("{ArrowRight}");
    const openai = screen.getByRole("radio", { name: "OpenAI" });
    expect(openai).toHaveAttribute("aria-checked", "true");
    expect(openai).toHaveFocus();
    expect(screen.getByLabelText("OpenAI API key")).toHaveAttribute(
      "placeholder",
      "sk-proj-…",
    );
    expect(screen.getByLabelText("Project ID (optional)")).toBeVisible();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByLabelText("OpenRouter API key")).toBeVisible();
    expect(screen.queryByLabelText(/ID \(optional\)/)).toBeNull();
    expect(
      screen.getByText(/OpenRouter account's settings decide which companies/),
    ).toBeVisible();
  });

  it("says a key needs its ID, and tests again with it", async () => {
    let withId = false;
    fakeBackend({
      "POST /settings/keys/openai/test": (body: { scope_id?: string }) => {
        withId = Boolean(body.scope_id);
        return withId ? {} : NEEDS_ID();
      },
    });
    open("/setup/key");
    await userEvent.click(screen.getByRole("radio", { name: "OpenAI" }));
    await userEvent.type(screen.getByLabelText("OpenAI API key"), "sk-proj-x");
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => {
      expect(document.getElementById("key-status")).toHaveTextContent(
        /isn't tied to one project/,
      );
    });
    const id = screen.getByLabelText("Project ID (optional)");
    expect(id).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    await userEvent.type(id, "proj_1");
    expect(document.getElementById("key-status")).toHaveTextContent(
      /isn't tied to one project/,
    );
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/Key works/)).toBeVisible();
    expect(tested("openai").at(-1)?.body).toEqual({
      key: "sk-proj-x",
      scope_id: "proj_1",
    });
  });

  it("counts a result only for the key it tested", async () => {
    fakeBackend({ "POST /settings/keys/anthropic/test": {} });
    open("/setup/key");
    await userEvent.type(
      screen.getByLabelText("Anthropic API key"),
      "sk-ant-x",
    );
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/Key works/)).toBeVisible();
    await userEvent.type(screen.getByLabelText("Anthropic API key"), "y");
    expect(screen.queryByText(/Key works/)).toBeNull();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  });

  it("shows why a key was refused, and goes on without one", async () => {
    fakeBackend({
      "POST /settings/keys/anthropic/test": refusal(
        "Anthropic didn't accept this key. Check it and paste it again.",
      ),
      "GET /models": MODELS,
    });
    open("/setup/key");
    await userEvent.type(screen.getByLabelText("Anthropic API key"), "bad");
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/didn't accept this key/)).toBeVisible();
    expect(screen.getByLabelText("Anthropic API key")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Continue without a key" }),
    );
    expect(
      await screen.findByRole("heading", { name: /search models/ }),
    ).toBeVisible();
  });
});

function taskModels(
  ready: Partial<Record<"anthropic" | "openai", boolean>>,
  needsId = false,
) {
  const sonnet = {
    id: "anthropic:claude-sonnet-5-5",
    name: "Claude Sonnet 5.5",
    provider: "anthropic",
    input_usd: 2,
    output_usd: 10,
    measured: true,
    listed: true,
  };
  const sol = {
    ...sonnet,
    id: "openai:gpt-6.1-sol",
    name: "GPT-6.1 Sol",
    provider: "openai",
  };
  return {
    providers: [
      {
        id: "anthropic",
        name: "Anthropic",
        key_ready: ready.anthropic ?? false,
        id_label: "Workspace ID",
        needs_id: false,
      },
      {
        id: "openai",
        name: "OpenAI",
        key_ready: ready.openai ?? false,
        id_label: "Project ID",
        needs_id: needsId,
      },
      {
        id: "openrouter",
        name: "OpenRouter",
        key_ready: false,
        id_label: null,
        needs_id: false,
      },
    ],
    tasks: [
      {
        task: "answer",
        field: "model_answer",
        chosen: sonnet.id,
        may_run_locally: true,
        models: [sonnet, sol],
      },
      {
        task: "deep",
        field: "model_deep",
        chosen: sol.id,
        may_run_locally: false,
        models: [sonnet, sol],
      },
    ],
  };
}

describe("the ready step, and keys", () => {
  it("says answers are local with no key, and still lists the tasks", async () => {
    // The notice, and the tasks set to a provider without a key.
    fakeBackend({ "GET /models/tasks": taskModels({}) });
    open("/setup/ready");
    expect(
      await screen.findByText(
        /No cloud key: answers come from the local model on this Mac until you add one in Settings, Keys and models\./,
      ),
    ).toBeVisible();
    const heading = screen.getByText(
      "Some tasks are still set to models you have no key for",
    );
    const note = heading.closest<HTMLElement>(".folder-row");
    if (!note) throw new Error("no note");
    expect(
      within(note)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual([
      "Answers: Claude Sonnet 5.5, which needs an Anthropic key",
      "Deep answers: GPT-6.1 Sol, which needs an OpenAI key",
    ]);
    expect(
      within(note).getByRole("button", { name: "Choose models" }),
    ).toBeVisible();
  });

  it("names each task still set to a provider without a key, with Choose models", async () => {
    fakeBackend({
      "GET /models/tasks": taskModels({ openai: true }),
      "PATCH /settings": {},
      "GET /settings/keys": [],
    });
    open("/setup/ready");
    const heading = await screen.findByText(
      "Some tasks are still set to models you have no key for",
    );
    const note = heading.closest<HTMLElement>(".folder-row");
    if (!note) throw new Error("no note");
    expect(
      within(note)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["Answers: Claude Sonnet 5.5, which needs an Anthropic key"]);
    await userEvent.click(
      within(note).getByRole("button", { name: "Choose models" }),
    );
    await waitFor(() => {
      expect(window.location.hash).toBe("#/settings/models");
    });
    const finished = calls.find((c) => c.method === "PATCH");
    expect(finished?.body).toEqual({ setup_done: true });
  });

  it("says a key that needs its ID isn't ready", async () => {
    fakeBackend({ "GET /models/tasks": taskModels({ anthropic: true }, true) });
    open("/setup/ready");
    expect(
      await screen.findByText(
        "Deep answers: GPT-6.1 Sol, whose OpenAI key needs its Project ID",
      ),
    ).toBeVisible();
  });

  it("says nothing more when every task's provider has its key", async () => {
    fakeBackend({
      "GET /models/tasks": taskModels({ anthropic: true, openai: true }),
    });
    open("/setup/ready");
    await screen.findByRole("heading", { name: "Indexing has started" });
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/models/tasks")).toBe(true);
    });
    expect(screen.queryByText(/No cloud key/)).toBeNull();
    expect(screen.queryByText(/still set to models/)).toBeNull();
  });
});

describe("the other steps' copy", () => {
  it.each(["key", "models", "folders", "ready"])(
    "names no company where any could answer, on the %s step",
    async (step) => {
      fakeBackend({
        "GET /settings": { setup_done: false, default_sensitivity: "standard" },
        "GET /models": MODELS,
      });
      open(`/setup/${step}`);
      await screen.findByRole("heading", { level: 1 });
      // Claude Desktop and Claude Code stay, and so does the card for them.
      const page = document.body.cloneNode(true) as HTMLElement;
      for (const card of page.querySelectorAll(".folder-row")) {
        if (card.textContent.includes("Install for Claude Desktop"))
          card.remove();
      }
      const text = page.textContent.replaceAll(/Claude (Desktop|Code)/g, "");
      expect(text).not.toMatch(/Claude/);
    },
  );
});
