// Tests of the built app's text check: "Claude" only as Claude Desktop, Claude Code,
// or one of Claude's models by name.
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { findClaude, scanBuiltApp } from "./app-text.mjs";

test("Claude Desktop, Claude Code and Claude's model names pass", () => {
  assert.deepEqual(
    findClaude(
      "Install for Claude Desktop · Copy Claude Code command · Claude Sonnet 5.5, Claude Opus 5.5, Claude Haiku 5.5",
    ),
    [],
  );
});

test("Claude for any provider is found, with where it is", () => {
  const found = findClaude(
    'children:"Ask Claude"},"Sent this page to Claude."',
  );
  assert.equal(found.length, 2);
  assert.match(found[0], /Ask Claude/);
  assert.match(found[1], /Sent this page to Claude\./);
});

test("a built app's scripts and pages are scanned, its other files not", () => {
  const folder = mkdtempSync(join(tmpdir(), "app-text-"));
  try {
    mkdirSync(join(folder, "assets"));
    writeFileSync(
      join(folder, "assets", "index.js"),
      "`Let Claude organize ${x}`",
    );
    writeFileSync(join(folder, "index.html"), "<p>Claude Desktop</p>");
    writeFileSync(join(folder, "assets", "font.woff2"), "Claude");
    const found = scanBuiltApp(folder);
    assert.equal(found.length, 1);
    assert.match(found[0], /^index\.js: .*Let Claude organize/);
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
});
