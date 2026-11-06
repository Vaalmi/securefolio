import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { api } from "./helpers";

// The keyboard walks for keys that need their Workspace or Project ID: on first run,
// in Settings, and a first-run key handed to Settings. The stub providers refuse a
// key with "-any-" in it unless its ID is sent (testing/e2e_stubs.py).

const status = (page: Page, id: string) => page.locator(`#${id}`);

async function press(page: Page, name: string | RegExp): Promise<void> {
  const button = page.getByRole("button", { name }).first();
  await button.focus();
  await page.keyboard.press("Enter");
}

test.afterEach(async () => {
  // The keys live in the backend's memory, shared by every test: put them back.
  for (const provider of ["anthropic", "openai"]) {
    await api(`/settings/keys/${provider}`, "DELETE");
  }
});

test("first run: a key that needs its Workspace ID says so, then works with it", async ({
  page,
}) => {
  await page.goto("/#/setup/key");
  await page.getByLabel("Anthropic API key").focus();
  await page.keyboard.type("sk-ant-any-test-key");
  await press(page, "Test");
  await expect(status(page, "key-status")).toContainText("Workspace ID");
  const id = page.getByLabel("Workspace ID (optional)");
  await expect(id).toHaveAttribute("aria-invalid", "true");
  await id.focus();
  await page.keyboard.type("wrkspc_test");
  await expect(status(page, "key-status")).toContainText("Workspace ID");
  await press(page, "Test");
  await expect(status(page, "key-status")).toContainText("Key works");
});

test("Settings: a new key that needs its Project ID, then a saved one whose ID was cleared", async ({
  page,
}) => {
  await page.goto("/#/settings/models");
  const row = page.locator(".setting-row", { hasText: "OpenAI" }).first();
  await row.getByRole("button", { name: "Add key" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#key-openai")).toBeFocused();
  await page.keyboard.type("sk-proj-any-test");
  await press(page, "Test and save");
  await expect(status(page, "key-openai-status")).toContainText("Project ID");
  await page.locator("#key-openai-scope").focus();
  await page.keyboard.type("proj_test");
  await press(page, "Test and save");
  const change = page.getByRole("button", {
    name: "Change the OpenAI Project ID",
  });
  await change.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#scope-openai")).toBeFocused();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.press("Backspace");
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("button", { name: "Add the OpenAI Project ID" }),
  ).toBeFocused();
  await row.getByRole("button", { name: "Test", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(status(page, "key-openai-status")).toContainText("Project ID");
});

test("a first-run key that needs its Workspace ID still asks for it in Settings, and Home answers on this Mac", async ({
  page,
}) => {
  await page.goto("/#/setup/key");
  await page.getByLabel("Anthropic API key").focus();
  await page.keyboard.type("sk-ant-any-handoff");
  await page.getByLabel("Workspace ID (optional)").focus();
  await page.keyboard.type("wrkspc_handoff");
  await press(page, "Test");
  await expect(status(page, "key-status")).toContainText("Key works");
  await page.goto("/#/settings/local");
  await expect(page.getByText("Model for each job")).toBeVisible();
  await page.goto("/#/settings/models");
  const change = page.getByRole("button", {
    name: "Change the Anthropic Workspace ID",
  });
  await change.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.press("Backspace");
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("button", { name: "Add the Anthropic Workspace ID" }),
  ).toBeFocused();
  // Its test with no ID is what finds that it needs one.
  const row = page.locator(".setting-row", { hasText: "sk-ant-" }).first();
  await row.getByRole("button", { name: "Test", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(status(page, "key-anthropic-status")).toContainText(
    "Workspace ID",
  );
  await page.goto("/#/home");
  await expect(
    page.getByRole("button", { name: /^Ask Local AI/ }),
  ).toBeVisible();
});
