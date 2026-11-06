import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, fakeBackend } from "../../fakeBackend";
import { installBrowser, open } from "../../testApp";

// Settings, Models for each task.

beforeEach(() => {
  installBrowser();
});

const SONNET = {
  id: "anthropic:claude-sonnet-5-5",
  name: "Claude Sonnet 5.5",
  provider: "anthropic",
  input_usd: 2,
  output_usd: 10,
  measured: true,
  listed: true,
};
const SOL = {
  id: "openai:gpt-6.1-sol",
  name: "GPT-6.1 Sol",
  provider: "openai",
  input_usd: 2,
  output_usd: 10,
  measured: false,
  listed: true,
};
const GPT_OSS = {
  id: "openrouter:openai/gpt-oss-120b",
  name: "OpenAI: gpt-oss-120b",
  provider: "openrouter",
  input_usd: 0.1,
  output_usd: 0.5,
  measured: false,
  listed: true,
};

const OPENROUTER_KEY = {
  provider: "openrouter",
  name: "OpenRouter",
  present: true,
  hint: "••••k1k1",
  id_label: null,
  scope_id: null,
  needs_id: false,
};

function task(name: string, chosen: string, models: object[], local = false) {
  return {
    task: name,
    field: `model_${name}`,
    chosen,
    may_run_locally: local,
    models,
  };
}

function provider(
  id: string,
  name: string,
  ready: boolean | undefined,
  idLabel: string | null,
) {
  return {
    id,
    name,
    key_ready: ready ?? false,
    id_label: idLabel,
    needs_id: false,
  };
}

function lists(
  ready: Partial<Record<"anthropic" | "openai" | "openrouter", boolean>>,
  tasks: object[],
) {
  return {
    providers: [
      provider("anthropic", "Anthropic", ready.anthropic, "Workspace ID"),
      provider("openai", "OpenAI", ready.openai, "Project ID"),
      provider("openrouter", "OpenRouter", ready.openrouter, null),
    ],
    tasks,
  };
}

function backend(found: object, extra: Record<string, unknown> = {}) {
  fakeBackend({
    "GET /settings": { setup_done: true, local_answers: "gemma4:26b" },
    "GET /models/tasks": found,
    "PATCH /settings": {},
    ...extra,
  });
}

function select(title: string): HTMLSelectElement {
  return screen.getByLabelText(`Model for ${title}`);
}

describe("Models for each task", () => {
  it("groups each task's models by provider, with prices and not measured", async () => {
    backend(
      lists({ anthropic: true, openai: true, openrouter: true }, [
        task("answer", SONNET.id, [SONNET, SOL, GPT_OSS], true),
      ]),
    );
    open("/settings/models");
    await screen.findByLabelText("Model for Answers");
    const groups = within(select("Answers")).getAllByRole("group");
    expect(groups.map((g) => g.getAttribute("label"))).toEqual([
      "Anthropic",
      "OpenAI",
      "OpenRouter",
      "On this Mac",
    ]);
    expect(
      within(groups[1]).getByRole("option", {
        name: "GPT-6.1 Sol · $2.00 in, $10.00 out · not measured",
      }),
    ).toBeEnabled();
    expect(
      screen.getByText("$2.00 in · $10.00 out per million tokens"),
    ).toBeVisible();
    expect(screen.queryByText("Not measured")).toBeNull();
  });

  it("says a chosen model isn't measured, and saves a choice", async () => {
    backend(
      lists({ anthropic: true, openai: true }, [
        task("answer", SOL.id, [SONNET, SOL]),
      ]),
    );
    open("/settings/models");
    expect(await screen.findByText("Not measured")).toBeVisible();
    await userEvent.selectOptions(select("Answers"), SONNET.id);
    await waitFor(() => {
      const saved = calls.find((c) => c.method === "PATCH");
      expect(saved?.body).toEqual({ model_answer: SONNET.id });
    });
  });

  it("sends no second choice past one still being saved", async () => {
    // An older save, answered late, overwrote a newer choice.
    let answer: ((value: Response) => void) | undefined;
    backend(
      lists({ anthropic: true, openai: true }, [
        task("answer", SOL.id, [SONNET, SOL]),
      ]),
      {
        "PATCH /settings": () =>
          new Promise<Response>((resolve) => {
            answer = resolve;
          }),
      },
    );
    open("/settings/models");
    await screen.findByLabelText("Model for Answers");
    await userEvent.selectOptions(select("Answers"), SONNET.id);
    await waitFor(() => {
      expect(select("Answers")).toBeDisabled();
    });
    answer?.(new Response("{}", { status: 200 }));
    await waitFor(() => {
      expect(select("Answers")).toBeEnabled();
    });
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1);
  });

  it("can't choose a provider with no ready key, and says the task runs locally", async () => {
    backend(
      lists({ anthropic: true }, [
        task("answer", SOL.id, [
          SONNET,
          SOL,
          { ...SOL, id: "openai:x", name: "X" },
        ]),
      ]),
    );
    open("/settings/models");
    await screen.findByLabelText("Model for Answers");
    const openai = within(select("Answers"))
      .getAllByRole("group")
      .find((g) => g.getAttribute("label") === "OpenAI: add a key to choose");
    expect(openai).toBeDefined();
    if (!openai) throw new Error("no OpenAI group");
    const options = within(openai).getAllByRole("option");
    expect(options.map((o) => (o as HTMLOptionElement).disabled)).toEqual([
      false, // chosen, so it still shows as chosen
      true,
    ]);
    expect(
      screen.getByText(
        "Needs an OpenAI key. Until then, this runs on the local model.",
      ),
    ).toBeVisible();
  });

  it("says reading a page is unavailable with no key, and Re-read still works", async () => {
    backend(lists({}, [task("page", SONNET.id, [SONNET])]));
    open("/settings/models");
    expect(
      await screen.findByText(
        "Needs an Anthropic key. Until then, reading a page in the cloud is unavailable; Re-read this page on your Mac still works.",
      ),
    ).toBeVisible();
  });

  it("says a chosen model is no longer listed", async () => {
    backend(
      lists({ openrouter: true }, [
        task("answer", GPT_OSS.id, [{ ...GPT_OSS, listed: false }]),
      ]),
    );
    open("/settings/models");
    expect(
      await screen.findByText(
        /OpenAI: gpt-oss-120b is no longer in OpenRouter's list/,
      ),
    ).toBeVisible();
    expect(
      within(select("Answers")).getByRole("option", {
        name: /no longer listed$/,
      }),
    ).toBeVisible();
  });

  it("reloads the lists when a key changes", async () => {
    backend(lists({}, [task("answer", SONNET.id, [SONNET])]), {
      "GET /settings/keys": [OPENROUTER_KEY],
      "GET /models/openrouter": { state: "fresh", fetched_at: 1, failed: null },
      "DELETE /settings/keys/openrouter": {},
    });
    open("/settings/models");
    await userEvent.click(
      await screen.findByRole("button", { name: "Remove the OpenRouter key" }),
    );
    const before = calls.filter((c) => c.path === "/models/tasks").length;
    await userEvent.click(screen.getByRole("button", { name: "Remove key" }));
    await waitFor(() => {
      expect(calls.filter((c) => c.path === "/models/tasks")).toHaveLength(
        before + 1,
      );
    });
  });

  it("says a saved key that needs its ID isn't ready yet", async () => {
    const found = lists({}, [task("answer", SOL.id, [SONNET, SOL])]);
    found.providers[1] = { ...found.providers[1], needs_id: true };
    backend(found);
    open("/settings/models");
    expect(
      await screen.findByText(
        "The OpenAI key needs its Project ID. Until then, this runs on the local model.",
      ),
    ).toBeVisible();
    expect(
      within(select("Answers"))
        .getAllByRole("group")
        .map((g) => g.getAttribute("label")),
    ).toContain("OpenAI: add its Project ID to choose");
  });

  it("reloads the lists when OpenRouter's list is refreshed", async () => {
    // A fresh list that drops the chosen model says so at once.
    let found = lists({ openrouter: true }, [
      task("answer", GPT_OSS.id, [GPT_OSS]),
    ]);
    backend(lists({}, []), {
      "GET /models/tasks": () => found,
      "GET /settings/keys": [OPENROUTER_KEY],
      "GET /models/openrouter": { state: "fresh", fetched_at: 1, failed: null },
      "POST /models/openrouter/refresh": () => {
        found = lists({ openrouter: true }, [
          task("answer", GPT_OSS.id, [{ ...GPT_OSS, listed: false }]),
        ]);
        return { state: "fresh", fetched_at: 2, failed: null };
      },
    });
    open("/settings/models");
    await screen.findByLabelText("Model for Answers");
    expect(screen.queryByText(/no longer in OpenRouter's list/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(
      await screen.findByText(/no longer in OpenRouter's list/),
    ).toBeVisible();
  });
});
