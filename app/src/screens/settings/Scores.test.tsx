import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { fakeBackend } from "../../fakeBackend";
import { installBrowser, open } from "../../testApp";

// Scores where you choose a model: a measured model's under the answering tasks; a local
// model's for the build that was measured; none for the helpers.

beforeEach(() => {
  installBrowser();
});

const GIB = 1024 ** 3;
const BUILD =
  "48eb98ec778c163d87f69d735fcdd79e345063af9af4685613fd018ae5debf5e";

function scores(changes: object = {}) {
  return {
    measured_on: "2026-11-14",
    questions: 60,
    judged: true,
    judge_model: "anthropic:claude-opus-5-5",
    judge_name: "Claude Opus 5.5",
    answers_correct: 0.9555555555555556,
    citation_precision: 0.9625,
    facts_found: 0.98,
    unanswerable: 0.8,
    answerable_refused: 0,
    ...changes,
  };
}

const SOL = {
  id: "openai:gpt-6.1-sol",
  name: "GPT-6.1 Sol",
  provider: "openai",
  input_usd: 2,
  output_usd: 10,
  measured: true,
  scores: scores(),
  listed: true,
};
const OPUS = {
  id: "anthropic:claude-opus-5-5",
  name: "Claude Opus 5.5",
  provider: "anthropic",
  input_usd: 4,
  output_usd: 20,
  measured: false,
  scores: null,
  listed: true,
};

function task(name: string, chosen: string) {
  return {
    task: name,
    field: `model_${name}`,
    chosen,
    may_run_locally: false,
    models: [SOL, OPUS],
  };
}

function cloud(answer: string) {
  const ready = (id: string, name: string) => ({
    id,
    name,
    key_ready: true,
    id_label: null,
    needs_id: false,
  });
  fakeBackend({
    "GET /settings": { setup_done: true, local_answers: "gemma4:26b" },
    "GET /models/tasks": {
      providers: [ready("anthropic", "Anthropic"), ready("openai", "OpenAI")],
      tasks: [
        task("answer", answer),
        task("deep", "openai:gpt-6.1-sol"),
        task("helper", "openai:gpt-6.1-sol"),
      ],
    },
  });
}

function local(measured: object | null) {
  const model = (name: string, kept: object | null) => ({
    name,
    size: 17 * GIB,
    text: true,
    images: true,
    jobs: ["local_answers", "local_cards", "local_figures"],
    memory: { gb: 24.7, about: false },
    measured: kept,
  });
  const job = (tag: string) => ({ tag, problem: null });
  fakeBackend({
    "GET /settings": { setup_done: true, offline: false },
    "GET /models": {
      search: { state: "installed", bytes_done: 9, bytes_total: 9 },
      page: { state: "installed", bytes_done: 9, bytes_total: 9 },
      jobs: {
        local_answers: job("gemma4:26b"),
        local_cards: job("gemma4:26b"),
        local_figures: job("gemma4:26b"),
      },
      installed: {
        local: [model("gemma4:26b", measured), model("qwen3.5:9b", null)],
        remote: [],
      },
      memory: { gb: 24.7, about: false },
      mac_memory_gb: 48,
      local: {
        tag: "gemma4:26b",
        recommended: "gemma4:26b",
        installed: true,
        pull: {
          tag: null,
          state: "not_installed",
          done: 0,
          total: 0,
          reason: null,
        },
      },
      ollama: {
        running: true,
        tags: ["gemma4:26b"],
        refused: null,
        no_cloud: true,
      },
    },
  });
}

function scoresUnder(name: string): HTMLElement {
  return screen.getByRole("group", { name: new RegExp(`^Scores ${name}`) });
}

describe("scores for a cloud model", () => {
  it("show under the Answers and Deep models, with their date and judge", async () => {
    cloud("openai:gpt-6.1-sol");
    open("/settings/models");
    await screen.findByLabelText("Model for Answers");
    const groups = screen.getAllByRole("group", {
      name: /^Scores from SecureFolio's evaluation/,
    });
    expect(groups).toHaveLength(2);
    const shown = groups[0];
    expect(shown).toHaveAccessibleName(
      "Scores from SecureFolio's evaluation on Nov 14, 2026, 60 questions · citations judged by Claude Opus 5.5",
    );
    expect(
      within(shown).getByText("Answers correct").nextSibling,
    ).toHaveTextContent("95.6%");
    expect(shown).toHaveTextContent("Citations supported96.3%");
    expect(shown).toHaveTextContent(
      "No-answer questions refused80.0%under the 90% target",
    );
    expect(shown).toHaveTextContent("Answerable questions refused0%");
    expect(
      within(screen.getByLabelText("Model for Answers")).getByRole("option", {
        name: /GPT-6.1 Sol/,
      }),
    ).toHaveTextContent("GPT-6.1 Sol · $2.00 in, $10.00 out · 95.6% correct");
    expect(
      within(screen.getByLabelText("Model for Helpers")).getByRole("option", {
        name: /GPT-6.1 Sol/,
      }),
    ).toHaveTextContent(/out$/);
  });

  it("aren't shown for a model never measured, which says so", async () => {
    cloud("anthropic:claude-opus-5-5");
    open("/settings/models");
    await screen.findByLabelText("Model for Answers");
    expect(await screen.findByText("Not measured")).toBeVisible();
    expect(screen.getAllByRole("group", { name: /^Scores from/ })).toHaveLength(
      1,
    );
    expect(scoresUnder("from")).toBeVisible();
  });
});

describe("scores for a local model", () => {
  it("show for the build that was measured, under Private and Offline answers", async () => {
    local({
      scores: scores({ answers_correct: 0.889 }),
      build: BUILD,
      changed: null,
    });
    open("/settings/local");
    await screen.findByLabelText("Model for Cards");
    const shown = scoresUnder("measured on this Mac");
    expect(shown).toHaveAccessibleName(
      "Scores measured on this Mac, build 48eb98ec, on Nov 14, 2026, 60 questions · citations judged by Claude Opus 5.5",
    );
    expect(shown).toHaveTextContent("Answers correct88.9%");
    expect(screen.getAllByRole("group", { name: /^Scores/ })).toHaveLength(1);
    expect(
      within(screen.getByLabelText("Model for Private and Offline answers"))
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual([
      "gemma4:26b · suggested · 88.9% correct",
      "qwen3.5:9b · not measured",
    ]);
  });

  it("say when the run had no judge", async () => {
    const unjudged = scores({
      judged: false,
      judge_model: null,
      judge_name: null,
      answers_correct: undefined,
      citation_precision: undefined,
      facts_found: 0.933,
    });
    local({ scores: unjudged, build: BUILD, changed: null });
    open("/settings/local");
    await screen.findByLabelText("Model for Cards");
    const shown = scoresUnder("measured on this Mac");
    expect(shown).toHaveAccessibleName(/citations not judged$/);
    expect(shown).toHaveTextContent("Facts found93.3%");
    expect(shown).toHaveTextContent("Citations supportednot judged");
  });

  it("aren't shown for a model rebuilt since, which says so", async () => {
    local({
      scores: null,
      build: null,
      changed: { measured: BUILD, installed: "9d04f31c6a12aa" },
    });
    open("/settings/local");
    await screen.findByLabelText("Model for Cards");
    expect(screen.queryByRole("group", { name: /^Scores/ })).toBeNull();
    expect(screen.getByText("Not measured")).toBeVisible();
    expect(
      screen.getByText(/gemma4:26b has changed since it was measured/),
    ).toHaveTextContent(
      "gemma4:26b has changed since it was measured: build 48eb98ec was measured, 9d04f31c is installed. Its old scores aren't shown.",
    );
  });

  it("aren't shown for a model never measured", async () => {
    local(null);
    open("/settings/local");
    await screen.findByLabelText("Model for Cards");
    expect(screen.queryByRole("group", { name: /^Scores/ })).toBeNull();
    expect(screen.getByText("Not measured")).toBeVisible();
    expect(screen.queryByText(/has changed since/)).toBeNull();
  });
});
