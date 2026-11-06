// Makes the desktop app's icons from the logo, run by hand when the logo changes:
//   node scripts/make-icons.mjs
// - The app icon: the logo on its indigo square, rounded as a macOS icon, rendered at
//   1024 px and cut into the sizes desktop/src-tauri/icons already holds by
//   `tauri icon`. Only those files are replaced.
// - The menu-bar icon: the logo's outline alone, black on clear, as a template image
//   macOS tints light or dark (icons/tray-template.png, loaded in `build_tray`).
// It renders in Playwright's Chromium, which the checks install, and needs no network.
import { execFileSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const ASSETS = join(ROOT, "app/src/assets");
const ICONS = join(ROOT, "desktop/src-tauri/icons");
const APP_SIZE = 1024;
// Apple's icon grid: an 824 px rounded square on the 1024 px canvas.
const TILE = 824;
const TRAY_SIZE = 44; // macOS shows a menu-bar icon 18 points high: 44 px is sharp at 2x
// The logo's own outline, from its clip path, squared: the menu bar has no room for
// the margin the full drawing keeps.
const OUTLINE_VIEW_BOX = "42.1 42.6 290 290";

function dataUrl(svg) {
  return `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`;
}

async function render(page, html, size, path) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(
    `<html><body style="margin:0;background:transparent">${html}</body></html>`,
  );
  // Runs in the page, so it's given as text.
  await page.waitForFunction(
    "Array.from(document.images).every((image) => image.complete)",
  );
  await page.screenshot({ path, omitBackground: true });
}

const tile = readFileSync(join(ASSETS, "securefolio_logo_bg.svg"), "utf8");
const outline = readFileSync(join(ASSETS, "securefolio_logo.svg"), "utf8")
  .replace(/viewBox="[^"]*"/, `viewBox="${OUTLINE_VIEW_BOX}"`)
  .replaceAll('fill="#f5f9f9"', 'fill="#000000"');

const work = mkdtempSync(join(tmpdir(), "securefolio-icons-"));
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  const appIcon = join(work, "app-icon.png");
  const inset = (APP_SIZE - TILE) / 2;
  await render(
    page,
    `<img src="${dataUrl(tile)}" style="position:absolute;left:${inset}px;top:${inset}px;width:${TILE}px;height:${TILE}px;border-radius:22.37%">`,
    APP_SIZE,
    appIcon,
  );
  await render(
    page,
    `<img src="${dataUrl(outline)}" style="display:block;width:${TRAY_SIZE}px;height:${TRAY_SIZE}px">`,
    TRAY_SIZE,
    join(ICONS, "tray-template.png"),
  );
  const made = join(work, "made");
  execFileSync("npx", ["tauri", "icon", appIcon, "--output", made], {
    cwd: join(ROOT, "desktop"),
    stdio: "inherit",
  });
  for (const name of readdirSync(ICONS)) {
    const from = join(made, name);
    if (
      name.endsWith(".png") ||
      name.endsWith(".icns") ||
      name.endsWith(".ico")
    ) {
      if (existsSync(from)) copyFileSync(from, join(ICONS, name));
    }
  }
  console.log(`Made the app icon and tray-template.png in ${ICONS}`);
} finally {
  await browser.close();
  rmSync(work, { recursive: true, force: true });
}
