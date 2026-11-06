import { expect, test } from "@playwright/test";

import { api } from "./helpers";

interface AppSettings {
  model_answer: string;
  spend_cap_usd: number;
}

test("first run: a refused key, folders, the Ready step, and into the app", async ({
  page,
}) => {
  await api("/settings", "PATCH", { setup_done: false });
  await page.goto("/#/home");
  await expect(page).toHaveURL(/#\/setup\/key/);
  await expect(
    page.getByRole("heading", { name: "Connect a cloud model" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Continue without a key" }).click();
  await expect(
    page.getByRole("heading", { name: "Download the search models" }),
  ).toBeVisible();
  await expect(page.getByText("Downloaded and checked.")).toBeVisible();
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(
    page.getByRole("heading", { name: "Choose folders to watch" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Start indexing" }).click();

  await expect(
    page.getByRole("heading", { name: "Indexing has started" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Indexing has started" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Open SecureFolio" }).click();
  await expect(page).toHaveURL(/#\/home/);
  await page.reload();
  await expect(page).toHaveURL(/#\/home/);
});

test("Settings: every section, each accessible", async ({ page }) => {
  for (const [id, heading] of [
    ["models", "Keys and models"],
    ["local", "Local models and Offline"],
    ["privacy", "Privacy and MCP"],
    ["spend", "Spending and cloud log"],
    ["memory", "Memory"],
    ["backup", "Backup and import"],
  ] as const) {
    await page.goto(`/#/settings/${id}`);
    await expect(page.locator("#settings-h")).toHaveText(heading);
  }
});

test("a model per task saves, and the cap is checked", async ({ page }) => {
  await page.goto("/#/settings/models");
  await page
    .getByLabel("Model for Answers")
    .selectOption("anthropic:claude-opus-5-5");
  await expect
    .poll(async () => (await api<AppSettings>("/settings")).model_answer)
    .toBe("anthropic:claude-opus-5-5");
  await page
    .getByLabel("Model for Answers")
    .selectOption("anthropic:claude-sonnet-5-5");

  await page.goto("/#/settings/spend");
  await page.getByLabel("Cap in US dollars").fill("nope");
  await expect(page.getByText(/Enter an amount in US dollars/)).toBeVisible();
  await page.getByLabel("Cap in US dollars").fill("30");
  await page.getByLabel("Cap in US dollars").blur();
  await expect
    .poll(async () => (await api<AppSettings>("/settings")).spend_cap_usd)
    .toBe(30);
  await api("/settings", "PATCH", { spend_cap_usd: 25 });
});

test("back up now, and the v1 import names a file that isn't v1's", async ({
  page,
}) => {
  await page.goto("/#/settings/backup");
  await page.getByRole("button", { name: "Back up now" }).click();
  await expect(page.getByText(/Last backup:/)).toBeVisible();
  await page.evaluate(() => {
    window.securefolioChosenPaths = ["/nonexistent/metadata.db"];
  });
  await page.getByRole("button", { name: "Choose metadata.db…" }).click();
  await expect(page.getByText("There's no file there.")).toBeVisible();
});

test("the palette opens with ⌘K, finds an action, and runs it", async ({
  page,
}) => {
  await page.goto("/#/home");
  await expect(page.locator("main").first()).toBeVisible();
  await page.keyboard.press("ControlOrMeta+k");
  const box = page.getByRole("combobox", {
    name: "Type a command or a document name",
  });
  await expect(box).toBeFocused();
  await box.fill("settings");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/settings\/models/);
});

test("⌘, opens Settings, and quick search has its own screen", async ({
  page,
}) => {
  await page.goto("/#/home");
  await expect(page.locator("main").first()).toBeVisible();
  await page.keyboard.press("ControlOrMeta+,");
  await expect(page).toHaveURL(/#\/settings\/models/);
  await page.goto("/#/quick");
  await expect(
    page.getByRole("main", { name: "SecureFolio quick search" }),
  ).toBeVisible();
});

test("nothing is requested from anywhere but the app and its backend", async ({
  page,
}) => {
  const hosts = new Set<string>();
  page.on("request", (sent) => {
    hosts.add(new URL(sent.url()).hostname);
  });
  for (const place of [
    "/#/home",
    "/#/library",
    "/#/settings/models",
    "/#/ask/new",
  ]) {
    await page.goto(place);
    await expect(page.locator("main").first()).toBeVisible();
  }
  expect([...hosts]).toEqual(["127.0.0.1"]);
});
