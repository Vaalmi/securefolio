// CI runs the checks one step at a time rather than through `npm run check`, so this
// keeps the two the same: every check `npm run check` runs is a step in the workflow.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

test("the workflow runs every check npm run check runs, and the e2e tests", () => {
  const { scripts } = JSON.parse(
    readFileSync(join(ROOT, "package.json"), "utf8"),
  );
  assert.match(scripts.check, /npm:check:\*/);
  assert.match(scripts.check, /npm run e2e/);
  const workflow = readFileSync(
    join(ROOT, ".github/workflows/check.yml"),
    "utf8",
  );
  const steps = new Set(
    workflow
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.startsWith("- run: npm run "))
      .map((line) => line.slice("- run: npm run ".length)),
  );
  const checks = Object.keys(scripts).filter((name) =>
    name.startsWith("check:"),
  );
  for (const name of [...checks, "e2e"]) {
    assert.ok(
      steps.has(name),
      `.github/workflows/check.yml doesn't run ${name}`,
    );
  }
});
