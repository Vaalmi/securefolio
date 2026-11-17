import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";

import { expect, test } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";

import { api, documentNamed } from "./helpers";
import { auditContrast } from "./contrast";
import { VAULT } from "./servers";

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

for (const theme of THEMES) {
  test(`a figure not in its sources says why on focus, ${theme}`, async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.goto("/#/home");
    const box = page.getByRole("combobox", {
      name: "Search or ask your library",
    });
    await box.fill("What does the roof work come to a year?");
    await expect(
      page.getByRole("button", { name: /^Ask (Cloud|Local) AI/ }),
    ).toBeEnabled();
    await box.press("Enter");
    const answer = page.getByRole("article", { name: "Answer" });
    await expect(answer).toBeVisible();
    await expect(answer.locator(".unshown")).toHaveText("$123,456.78");
    const tag = answer.getByRole("button", { name: /Not in its sources/ });
    const tip = page.getByRole("tooltip");
    await expect(tip).toBeHidden();
    // Keyboard alone: from the sentence's citation, Tab reaches its tag.
    await answer.locator("button.cite").last().focus();
    await page.keyboard.press("Tab");
    await expect(tag).toBeFocused();
    await expect(tip).toBeVisible();
    await expect(tip).toContainText("$123,456.78 isn't in the passage");
    // Every violation axe finds, whatever its weight.
    const problems = (await runAxe(page)).map(
      (v) => `${v.id} (${v.impact ?? "no impact"}) ${v.nodes.join(", ")}`,
    );
    problems.push(...(await page.evaluate(auditContrast)));
    expect(problems).toEqual([]);
    await page.keyboard.press("Escape");
    await expect(tip).toBeHidden();
  });
}

const OLLAMA_STUB = "http://127.0.0.1:8102"; // testing/e2e_stubs.py, OLLAMA_PORT

// The backend looks at Ollama again at most every few seconds; asking the route makes
// it look, so the switch is waited on until the route itself says so.
async function localModel(gone: boolean): Promise<void> {
  const path = gone ? "/stub/models-gone" : "/stub/models-back";
  await fetch(`${OLLAMA_STUB}${path}`, { method: "POST", body: "{}" });
  await expect
    .poll(
      async () =>
        Boolean(
          (await api<{ blocked: string | null }>("/ask/route?mode=standard"))
            .blocked,
        ),
      { timeout: 15_000 },
    )
    .toBe(gone);
}

interface Answering {
  model_answer: string;
  model_helper: string;
}

interface InstalledLocal {
  installed: {
    local: {
      name: string;
      measured: { changed: { installed: string | null } | null } | null;
    }[];
  };
}

/** A Settings row, by its control's label. */
function rowOf(page: Page, label: string): Locator {
  return page.locator(".setting-row", { has: page.getByLabel(label) });
}

async function axeProblems(page: Page): Promise<string[]> {
  const problems = (await runAxe(page)).map(
    (v) => `${v.id} (${v.impact ?? "no impact"}) ${v.nodes.join(", ")}`,
  );
  problems.push(...(await page.evaluate(auditContrast)));
  return problems;
}

test("Settings shows each answering model's scores: measured, not, and rebuilt", async ({
  page,
}) => {
  const before = await api<{ model_answer: string; model_deep: string }>(
    "/settings",
  );
  const kept = resolve(VAULT, "models", "measured.json");
  await api("/settings", "PATCH", {
    model_answer: "anthropic:claude-sonnet-5-5",
    model_deep: "anthropic:claude-opus-5-5",
  });
  try {
    await page.goto("/#/settings/models");
    const answers = page.getByRole("group", {
      name: /^Scores from SecureFolio's evaluation on Nov 14, 2026, 60 questions · citations judged by Claude Opus 5.5$/,
    });
    await expect(answers).toHaveCount(1); // Sonnet's; Opus isn't measured
    await expect(answers).toContainText("Answers correct97.8%");
    await expect(
      rowOf(page, "Model for Deep answers").getByText("Not measured", {
        exact: true,
      }),
    ).toBeVisible(); // Opus isn't measured
    // Keyboard alone: Tab goes from the Answers model to the Deep one, the scores
    // between them only read.
    await page.getByLabel("Model for Answers").focus();
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("Model for Deep answers")).toBeFocused();
    expect(await axeProblems(page)).toEqual([]);

    // A local model: measured for the build installed, then rebuilt since.
    mkdirSync(dirname(kept), { recursive: true });
    const write = (build: string) => {
      const scores = {
        measured_on: "2026-11-14",
        questions: 60,
        judged: true,
        judge_model: "anthropic:claude-opus-5-5",
        answers_correct: 0.889,
        citation_precision: 0.9625,
        unanswerable: 0.867,
        answerable_refused: 0.044,
      };
      writeFileSync(
        kept,
        JSON.stringify({
          models: { "gemma4:26b": { digest: build, ...scores } },
        }),
      );
    };
    write("0".repeat(60));
    const found = await api<InstalledLocal>("/models");
    const gemma = found.installed.local.find((m) => m.name === "gemma4:26b");
    const installed = gemma?.measured?.changed?.installed ?? "";
    expect(installed).not.toBe("");
    write(installed);
    await page.goto("/#/settings/local");
    const local = page.getByRole("group", {
      name: /^Scores measured on this Mac, build \w{8}, on Nov 14, 2026/,
    });
    await expect(local).toContainText("Answers correct88.9%");
    await page.getByLabel("Model for Private and Offline answers").focus();
    await page.keyboard.press("Tab");
    await expect(page.getByLabel("Model for Cards")).toBeFocused();
    expect(await axeProblems(page)).toEqual([]);

    write("0".repeat(60));
    await page.reload();
    await expect(
      page.getByText(/gemma4:26b has changed since it was measured/),
    ).toBeVisible();
    await expect(page.getByRole("group", { name: /^Scores/ })).toHaveCount(0);
    await expect(
      rowOf(page, "Model for Private and Offline answers").getByText(
        "Not measured",
        { exact: true },
      ),
    ).toBeVisible();
    expect(await axeProblems(page)).toEqual([]);
  } finally {
    rmSync(kept, { force: true });
    await api("/settings", "PATCH", {
      model_answer: before.model_answer,
      model_deep: before.model_deep,
    });
  }
});

test("with no AI to answer, Find passages shows what search finds", async ({
  page,
}) => {
  const before = await api<Answering>("/settings");
  // Local AI answers and helps, and its model is gone from Ollama.
  await api("/settings", "PATCH", {
    model_answer: "local",
    model_helper: "local",
  });
  await localModel(true);
  try {
    await page.goto("/#/home");
    const box = page.getByRole("combobox", {
      name: "Search or ask your library",
    });
    await box.fill("What does the roof report recommend?");
    const find = page.getByRole("button", { name: /^Find passages/ });
    await expect(find).toBeEnabled();
    await box.press("Enter");
    const found = page.getByRole("article", {
      name: "Passages found, not an answer",
    });
    await expect(
      found.getByRole("heading", { name: "Passages found, no AI answer" }),
    ).toBeVisible();
    await expect(found).toContainText("is no longer in Ollama");
    await expect(found.locator(".passage").first()).toBeVisible();
    // Keyboard alone: a passage's Preview opens it.
    await found
      .getByRole("button", { name: /^Preview / })
      .first()
      .focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Preview" })).toBeVisible();
    const followUp = page.getByRole("textbox", { name: "Ask a follow-up" });
    await followUp.fill("And what will it cost?");
    await followUp.press("Enter");
    await expect(found).toHaveCount(2);
    await expect(found.nth(1)).toContainText("Found with your words alone.");
    const problems = (await runAxe(page)).map(
      (v) => `${v.id} (${v.impact ?? "no impact"}) ${v.nodes.join(", ")}`,
    );
    problems.push(...(await page.evaluate(auditContrast)));
    expect(problems).toEqual([]);
  } finally {
    await localModel(false);
    await api("/settings", "PATCH", {
      model_answer: before.model_answer,
      model_helper: before.model_helper,
    });
  }
});

interface SavedReport {
  question: string;
  earlier: string[];
  expected_document_id: string | null;
  expected_place: { kind: string } | null;
  answer_facts: string[];
  note: string;
  private: boolean;
  reported: { thread: string; message: string };
}

/** The reports saved from one chat, as the vault's own set holds them. */
function reportsOf(page: Page): SavedReport[] {
  const thread = /#\/ask\/([0-9a-f]{32})/.exec(page.url())?.[1];
  const saved = JSON.parse(
    readFileSync(resolve(VAULT, "evals", "my_questions.json"), "utf8"),
  ) as SavedReport[];
  return saved.filter((r) => r.reported.thread === thread);
}

/** Tab, up to a limit, until the focused element is the one named: by its label,
 * or by its own words. */
async function tabTo(page: Page, name: string): Promise<void> {
  for (let i = 0; i < 12; i += 1) {
    await page.keyboard.press("Tab");
    const at = await page.evaluate(() => {
      const e = document.activeElement as HTMLInputElement | null;
      const label = e?.labels?.[0]?.textContent;
      return label ?? e?.textContent ?? "";
    });
    if (at.includes(name)) return;
  }
  throw new Error(`Tab never reached ${name}`);
}

async function ask(page: Page, box: string, question: string): Promise<void> {
  const dock = page.getByRole("textbox", { name: box });
  await dock.fill(question);
  // The Ask button waits for who would answer; then Return asks.
  await expect(
    page.getByRole("button", { name: /^Ask (Cloud|Local) AI/ }),
  ).toBeEnabled();
  await dock.press("Enter");
}

test("This was wrong, by keyboard, saves a follow-up with the question before it", async ({
  page,
}) => {
  await page.goto("/#/ask/new");
  await ask(page, "Ask a question", "What does the roof report recommend?");
  const answers = page.getByRole("article", { name: "Answer" });
  await expect(answers).toHaveCount(1);
  await ask(page, "Ask a follow-up", "And what will it cost?");
  await expect(answers).toHaveCount(2);
  await answers.nth(1).getByRole("button", { name: "This was wrong" }).focus();
  await page.keyboard.press("Enter");
  const form = page.getByRole("form", { name: "This was wrong" });
  await expect(form).toBeFocused();
  await expect(form).toContainText(
    "Saved with the question before it in this chat",
  );
  const problems = (await runAxe(page)).map(
    (v) => `${v.id} (${v.impact ?? "no impact"}) ${v.nodes.join(", ")}`,
  );
  problems.push(...(await page.evaluate(auditContrast)));
  expect(problems).toEqual([]);
  await tabTo(page, "The fact it should have held");
  await page.keyboard.type("Replace the flashing");
  await tabTo(page, "Note");
  await page.keyboard.type("It named no cost.");
  await tabTo(page, "Save report");
  await page.keyboard.press("Enter");
  await expect(
    page.getByText("Saved as a question in your own set, on this Mac."),
  ).toBeVisible();
  await expect(
    answers.nth(1).getByRole("button", { name: "Reported wrong" }),
  ).toBeFocused();
  const reports = reportsOf(page);
  expect(reports).toHaveLength(1);
  const [saved] = reports;
  expect(saved.question).toBe("And what will it cost?");
  expect(saved.earlier).toEqual(["What does the roof report recommend?"]);
  expect(saved.answer_facts).toEqual(["Replace the flashing"]);
  expect(saved.note).toBe("It named no cost.");
  expect(saved.private).toBe(false);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Reported wrong" }),
  ).toBeVisible();
});

test("a Private chat's report says it's Private, and why", async ({ page }) => {
  await page.goto("/#/ask/new?scope=all-private");
  await ask(page, "Ask a question", "What does the clinic visit say?");
  const answer = page.getByRole("article", { name: "Answer" });
  await expect(answer).toBeVisible();
  await answer.getByRole("button", { name: "This was wrong" }).click();
  const form = page.getByRole("form", { name: "This was wrong" });
  await expect(form).toContainText("Private, because this chat is Private.");
  await form.getByRole("radio", { name: "It isn't in my library" }).check();
  await form.getByRole("button", { name: "Save report" }).click();
  await expect(
    answer.getByRole("button", { name: "Reported wrong" }),
  ).toBeVisible();
  const reports = reportsOf(page);
  expect(reports).toHaveLength(1);
  const [saved] = reports;
  expect(saved.private).toBe(true);
  expect(saved.expected_document_id).toBeNull();
});

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
