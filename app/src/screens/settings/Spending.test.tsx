import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { fakeBackend } from "../../fakeBackend";
import { installBrowser, open } from "../../testApp";

// Settings, Spending and cloud log: a Provider column, each provider's model names,
// and a cap over every provider.

beforeEach(() => {
  installBrowser();
});

function call(id: number, provider: string, name: string, model: string) {
  return {
    id,
    at: 1_793_900_000 - id,
    provider,
    provider_name: name,
    model,
    model_name: model.split(":")[1] === "gpt-6-luna" ? "GPT-6 Luna" : model,
    purpose: "card",
    outcome: "sent",
    document_ids: ["d1"],
    input_tokens: 100,
    output_tokens: 20,
    cost_usd: 0.001,
  };
}

describe("the cloud log", () => {
  it("names each call's provider and model, for every provider", async () => {
    fakeBackend({
      "GET /spending": {
        spent_usd: 1.5,
        cap_usd: 25,
        prices_as_of: "2026-11-05",
        log: [
          {
            ...call(1, "anthropic", "Anthropic", "anthropic:claude-haiku-5-5"),
            model_name: "Claude Haiku 5.5",
          },
          call(2, "openai", "OpenAI", "openai:gpt-6-luna"),
          {
            ...call(3, "openrouter", "OpenRouter", "openrouter:someone/x"),
            model_name: "someone/x",
          },
        ],
      },
    });
    open("/settings/spend");
    const table = await screen.findByRole("table", { name: "Cloud calls" });
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((h) => h.textContent),
    ).toEqual([
      "When",
      "What",
      "Provider",
      "Model",
      "Tokens",
      "Documents",
      "Cost",
    ]);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(
      rows.map((r) => {
        const cells = within(r).getAllByRole("cell");
        return [cells[2]?.textContent, cells[3]?.textContent];
      }),
    ).toEqual([
      ["Anthropic", "Claude Haiku 5.5"],
      ["OpenAI", "GPT-6 Luna"],
      ["OpenRouter", "someone/x"],
    ]);
    expect(
      screen.getByText(
        /The cap covers every provider together: Anthropic, OpenAI and OpenRouter\./,
      ),
    ).toBeVisible();
  });
});
