import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, fakeBackend, ORGANIZE_ESTIMATE } from "../fakeBackend";
import { installBrowser, open } from "../testApp";

// Cloud organizing: so named, naming the helper model, its provider and its price, and
// off without that provider's key.

beforeEach(() => {
  installBrowser();
});

const SOURCE = {
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
  items: 120,
};

async function openDialog(estimate: object): Promise<HTMLElement> {
  fakeBackend({
    "GET /sources": [SOURCE],
    "GET /sources/s1/organize-estimate": estimate,
  });
  open("/sources");
  expect(
    await screen.findByRole("columnheader", { name: "Cloud organizing" }),
  ).toBeVisible();
  await userEvent.click(
    await screen.findByRole("switch", { name: "Organize Home in the cloud" }),
  );
  return screen.findByRole("dialog", { name: "Organize Home in the cloud?" });
}

describe("cloud organizing", () => {
  it("names the helper model, its provider and its batch price", async () => {
    // The batch rates shown are the backfill's, half the full ones.
    const dialog = await openDialog({
      ...ORGANIZE_ESTIMATE,
      batched: true,
      backfill_input_usd: 0.05,
      backfill_output_usd: 0.25,
    });
    expect(
      await within(dialog).findByText(
        "Cloud AI (Claude Haiku 5.5, from Anthropic) writes the cards for this folder instead of Local AI.",
      ),
    ).toBeVisible();
    expect(dialog).toHaveTextContent(
      "The documents here now go at the batch price, $0.05 in · $0.25 out per million tokens, half the full price. Each new document after goes at full price, $0.10 in · $0.50 out per million tokens.",
    );
    expect(dialog.textContent).not.toMatch(/Claude organiz|Let Claude/);
  });

  it("says full price when the backfill isn't batched", async () => {
    const dialog = await openDialog(ORGANIZE_ESTIMATE);
    expect(
      await within(dialog).findByText(
        "At full price: $0.10 in · $0.50 out per million tokens.",
      ),
    ).toBeVisible();
  });

  it("says OpenRouter's is full price", async () => {
    const dialog = await openDialog({
      ...ORGANIZE_ESTIMATE,
      model: "openrouter:openai/gpt-oss-120b",
      model_name: "OpenAI: gpt-oss-120b",
      provider: "OpenRouter",
    });
    expect(
      await within(dialog).findByText(
        /At full price, since OpenRouter has no batch price: \$0\.10 in/,
      ),
    ).toBeVisible();
  });

  it("can't be turned on without the helper's provider key, saying why", async () => {
    const why =
      "GPT-6 Luna needs an OpenAI key (or its Project ID). Add it in Settings, or choose another model.";
    const dialog = await openDialog({
      ...ORGANIZE_ESTIMATE,
      model: "openai:gpt-6-luna",
      model_name: "GPT-6 Luna",
      provider: "OpenAI",
      key_ready: false,
      unavailable: why,
    });
    const turnOn = await within(dialog).findByRole("button", {
      name: /Turn on/,
    });
    expect(turnOn).toBeDisabled();
    expect(dialog).toHaveTextContent(`Can't be turned on yet: ${why}`);
    await userEvent.click(turnOn);
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("shows why it wasn't turned on, and the estimate as it is now", async () => {
    // It goes on only at the helper and price shown, so a changed one is refused and
    // the new estimate shown.
    const changed =
      "The helper model or its price has changed since this estimate was shown. Check the new estimate, then turn it on again.";
    let estimate: object = ORGANIZE_ESTIMATE;
    fakeBackend({
      "GET /sources": [SOURCE],
      "GET /sources/s1/organize-estimate": () => estimate,
      "PATCH /sources/s1": () => {
        estimate = {
          ...ORGANIZE_ESTIMATE,
          backfill_usd: 125,
          model: "openai:gpt-6-astra",
          model_name: "GPT-6 Astra",
          provider: "OpenAI",
          input_usd: 10,
          output_usd: 50,
        };
        return new Response(JSON.stringify({ detail: changed }), {
          status: 409,
        });
      },
    });
    open("/sources");
    await userEvent.click(
      await screen.findByRole("switch", { name: "Organize Home in the cloud" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      await within(dialog).findByRole("button", { name: /about \$1.25/ }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(changed);
    expect(
      await within(dialog).findByRole("button", { name: /about \$125/ }),
    ).toBeVisible();
    expect(dialog).toHaveTextContent("GPT-6 Astra");
  });
});
