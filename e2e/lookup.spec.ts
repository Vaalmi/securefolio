import { writeFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { api } from "./helpers";

const LOOKUPS = 30;
const TARGET_MS = 10_000;

interface Row {
  id: string;
  title: string;
  status: string;
  sensitivity: string;
}

// From the quick-search window to the document open: type part of a name, press Return.
test("finding a known document takes under 10 s at the median", async ({
  page,
}) => {
  test.setTimeout(300_000);
  const { rows } = await api<{ rows: Row[] }>("/library?limit=500");
  const open = rows.filter(
    (row) => row.status === "indexed" && row.sensitivity === "standard",
  );
  const times: number[] = [];
  for (let at = 0; at < LOOKUPS; at += 1) {
    const row = open[at % open.length];
    await page.goto("/#/quick");
    const begun = Date.now();
    const box = page.getByRole("combobox").first();
    await box.fill(row.title);
    await expect(page.getByRole("option").first()).toBeVisible();
    // Journey 2: Command-Return (Control-Return off the Mac) opens the first match.
    await box.press("ControlOrMeta+Enter");
    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      row.title,
    );
    times.push(Date.now() - begun);
  }
  times.sort((a, b) => a - b);
  const median = times[Math.floor(times.length / 2)] ?? Infinity;
  writeFileSync(
    "e2e/test-results/lookup.json",
    JSON.stringify({ lookups: times.length, median_ms: median }),
  );
  expect(median).toBeLessThan(TARGET_MS);
});
