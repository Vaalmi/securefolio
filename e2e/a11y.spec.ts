import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { documentNamed } from "./helpers";
import { auditContrast } from "./contrast";

const require = createRequire(import.meta.url);
const AXE = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const THEMES = ["light", "dark"] as const;
const WIDTHS = [1440, 900] as const;
const FILES = [
  "Apartment lease.pdf",
  "Household budget.xlsx",
  "Budget deck.pptx",
  "Moving checklist.md",
  "Lease email.eml",
  "Moving plan.docx",
  "Photo.jpeg",
  "In the cloud.pdf",
];

interface Violation {
  id: string;
  impact: string | null;
  nodes: string[];
}

async function runAxe(page: Page): Promise<Violation[]> {
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async () => {
    const axe = (
      window as unknown as {
        axe: {
          run: (
            context: Document,
            options: object,
          ) => Promise<{
            violations: {
              id: string;
              impact: string | null;
              nodes: { target: string[] }[];
            }[];
          }>;
        };
      }
    ).axe;
    const out = await axe.run(document, {
      resultTypes: ["violations"],
      runOnly: {
        type: "tag",
        values: [
          "wcag2a",
          "wcag2aa",
          "wcag21a",
          "wcag21aa",
          "wcag22aa",
          "best-practice",
        ],
      },
    });
    return out.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      nodes: v.nodes.slice(0, 3).map((n) => n.target.join(" ")),
    }));
  });
}

async function routes(): Promise<string[]> {
  const opened: string[] = [];
  for (const file of FILES) {
    opened.push(`/#/doc/${(await documentNamed(file)).id}`);
  }
  return [
    "/#/home",
    "/#/home?tray=all",
    "/#/home?tray=skipped",
    "/#/library",
    "/#/library?panel=1",
    "/#/library?import=1",
    "/#/sources",
    "/#/collections",
    "/#/notes",
    "/#/chats",
    "/#/ask/new",
    "/#/home?palette=1",
    "/#/quick",
    "/#/setup/key",
    "/#/setup/models",
    "/#/setup/folders",
    "/#/setup/ready",
    "/#/settings/models",
    "/#/settings/local",
    "/#/settings/privacy",
    "/#/settings/spend",
    "/#/settings/memory",
    "/#/settings/backup",
    ...opened,
  ];
}

for (const theme of THEMES) {
  for (const width of WIDTHS) {
    test(`axe and contrast, ${theme} at ${String(width)} px`, async ({
      page,
    }) => {
      test.setTimeout(240_000);
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await page.setViewportSize({ width, height: 900 });
      const problems: string[] = [];
      for (const route of await routes()) {
        await page.goto(route);
        await expect(page.locator("[aria-busy=true]")).toHaveCount(0);
        await expect(page.locator("main").first()).toBeVisible();
        const found = await runAxe(page);
        problems.push(
          ...found
            .filter((v) => ["serious", "critical"].includes(v.impact ?? ""))
            .map((v) => `${route} ${v.id} ${v.nodes.join(", ")}`),
        );
        for (const fail of await page.evaluate(auditContrast)) {
          problems.push(`${route} ${fail}`);
        }
      }
      expect(problems).toEqual([]);
    });
  }
}

test("below 960 px the panel slides over, holding focus, and gives it back", async ({
  page,
}) => {
  await page.setViewportSize({ width: 900, height: 800 });
  await page.goto("/#/library");
  const panel = page.locator("#right-panel");
  await expect(page.getByRole("grid")).toBeVisible();
  await expect(panel).toHaveAttribute("inert", "");
  const toggle = page.getByRole("button", { name: "Inspector", exact: true });
  await toggle.click();
  await expect(panel).not.toHaveAttribute("inert", "");
  await expect(page.locator(".sidebar")).toHaveAttribute("inert", "");
  await expect(panel.locator(":focus")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(toggle).toBeFocused();
  await expect(page.locator(".sidebar")).not.toHaveAttribute("inert", "");
});

test("a keyboard walk: search, open, back, tray and import", async ({
  page,
}) => {
  await page.goto("/#/home");
  const box = page.getByRole("combobox", {
    name: "Search or ask your library",
  });
  await expect(box).toBeFocused();
  await page.keyboard.type("Apartment lease");
  await expect(page.getByRole("option").first()).toBeVisible();
  await page.keyboard.press("ControlOrMeta+Enter");
  await expect(page).toHaveURL(/#\/doc\//);
  await page.keyboard.press("Control+Shift+I");
  await expect(page.getByRole("region", { name: "Import tray" })).toBeVisible();
  await expect(page.getByRole("tab", { name: /All/ })).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /Active/ })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("region", { name: "Import tray" })).toHaveCount(
    0,
  );
  await page.keyboard.press("Control+O");
  await expect(
    page.getByRole("dialog", { name: "Import files" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
