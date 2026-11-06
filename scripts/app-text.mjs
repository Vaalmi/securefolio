// The built app's text names no company where any provider could answer: "Claude"
// only as Claude Desktop, Claude Code, or one of Claude's own models by name.
// scripts/bundle.mjs runs it over the built app, so a screen that says "Claude" for
// any provider fails the check.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

// What may follow "Claude": the two apps, and the model names a model list shows.
const ALLOWED = /^Claude (Desktop|Code|(Sonnet|Opus|Haiku) \d)/;
const AROUND = 40;

/** Each "Claude" in this text that isn't one of the allowed names, with its context. */
export function findClaude(text) {
  const found = [];
  for (const match of text.matchAll(/Claude/g)) {
    const at = match.index;
    if (ALLOWED.test(text.slice(at, at + 24))) continue;
    found.push(
      text.slice(Math.max(0, at - AROUND), at + AROUND).replace(/\s+/g, " "),
    );
  }
  return found;
}

/** Each such "Claude" in the built app's scripts and pages, under this folder. */
export function scanBuiltApp(folder) {
  const found = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (/\.(js|html)$/.test(entry.name)) {
        for (const context of findClaude(readFileSync(path, "utf8"))) {
          found.push(`${entry.name}: …${context}…`);
        }
      }
    }
  };
  walk(folder);
  return found;
}
