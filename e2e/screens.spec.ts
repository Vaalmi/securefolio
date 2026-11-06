import { mkdirSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { api } from "./helpers";

// Screenshots of the app's screens, light and dark, at 1440 and 900 px. Run only when
// asked, with SECUREFOLIO_SCREENSHOTS=1; they're looked at, not compared by machine.

const OUT = "e2e/test-results/screens";
// Each screen with the label of what shows once it has loaded.
const SCREENS: [string, string, string][] = process.env.SECUREFOLIO_SCREENSHOTS
  ? [
      ["settings-models", "/#/settings/models", "Model for Answers"],
      ["settings-local", "/#/settings/local", "Model for Cards"],
      ["setup-key", "/#/setup/key", "Anthropic API key"],
    ]
  : [];

for (const [name, path, loaded] of SCREENS) {
  for (const scheme of ["light", "dark"] as const) {
    for (const width of [1440, 900]) {
      test(`${name} ${scheme} ${String(width)}`, async ({ page }) => {
        await api("/settings", "PATCH", { setup_done: true });
        mkdirSync(OUT, { recursive: true });
        await page.emulateMedia({ colorScheme: scheme });
        await page.setViewportSize({ width, height: 1000 });
        await page.goto(path);
        await expect(page.getByLabel(loaded).first()).toBeVisible();
        await page.screenshot({
          path: `${OUT}/${name}__${scheme}-${String(width)}.png`,
          fullPage: true,
        });
      });
    }
  }
}
