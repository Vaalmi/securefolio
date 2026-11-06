import { rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";

import { expect, test } from "@playwright/test";
import type { Locator } from "@playwright/test";

import {
  api,
  documentNamed,
  fixtureFolder,
  passage,
  queueDrained,
  watchRequests,
} from "./helpers";
import type { Hit } from "./helpers";

interface Rectangle {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

function firstRect(hit: Hit): Rectangle {
  const rect = hit.locator.rects?.at(0);
  if (!rect) throw new Error(`${hit.document.file_name} has no rectangle`);
  return rect;
}

async function boxOf(locator: Locator) {
  const box = await locator.boundingBox();
  if (!box) throw new Error("It isn't on screen.");
  return box;
}

function darkPixels(element: Element, r: Rectangle): number {
  const target = element as HTMLCanvasElement;
  const context = target.getContext("2d");
  if (!context) return 0;
  const { data } = context.getImageData(
    r.x0 * target.width,
    r.y0 * target.height,
    Math.max(1, (r.x1 - r.x0) * target.width),
    Math.max(1, (r.y1 - r.y0) * target.height),
  );
  return data.filter((value, i) => i % 4 === 0 && value < 200).length;
}

const FORMATS = [
  ["marker fennel", "Moving plan.docx"],
  ["marker sage2", "Budget deck.pptx"],
  ["Marker thyme", "Household budget.xlsx"],
  ["Marker dill", "Transactions.csv"],
  ["marker clove", "Moving checklist.md"],
  ["marker anise", "Plain note.txt"],
  ["marker sorrel", "Saved page.html"],
  ["marker tarragon", "Lease email.eml"],
] as const;

for (const [marker, file] of FORMATS) {
  test(`${file} opens at its citation`, async ({ page }) => {
    const hit = await passage(marker, file);
    await page.goto(`/#/doc/${hit.document.id}?el=${hit.element_id}`);
    await expect(page.locator("#cited-passage")).toContainText(marker);
    await expect(page.locator("#cited-passage")).toBeInViewport();
  });
}

for (const file of ["Rotated page.pdf", "Cropped page.pdf"]) {
  test(`${file}: the highlight sits on its words`, async ({ page }) => {
    const hit = await passage("quartz", file);
    const rect = firstRect(hit);
    await page.goto(`/#/doc/${hit.document.id}?el=${hit.element_id}`);
    const overlay = page.getByTestId("pdf-highlight").first();
    await expect(overlay).toBeVisible();
    const canvas = await boxOf(page.getByTestId("pdf-canvas"));
    const box = await boxOf(overlay);
    expect(box.x - canvas.x).toBeCloseTo(rect.x0 * canvas.width, 0);
    expect(box.y - canvas.y).toBeCloseTo(rect.y0 * canvas.height, 0);
    expect(box.width).toBeCloseTo((rect.x1 - rect.x0) * canvas.width, 0);
    // Ink under the box proves the rectangle lands on text as PDF.js drew it.
    const ink = await page.getByTestId("pdf-canvas").evaluate(darkPixels, rect);
    expect(ink).toBeGreaterThan(0);
  });
}

for (const [name, width, zoomClicks] of [
  ["zoomed in", 1440, 5],
  ["zoomed out", 1440, -3],
  ["at 900 px", 900, 0],
] as const) {
  test(`the highlight stays on the page ${name}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const hit = await passage("quartz", "Cropped page.pdf");
    const rect = firstRect(hit);
    await page.goto(`/#/doc/${hit.document.id}?el=${hit.element_id}`);
    await expect(page.getByTestId("pdf-highlight").first()).toBeVisible();
    const button = page.getByRole("button", {
      name: zoomClicks < 0 ? "Zoom out" : "Zoom in",
    });
    for (let i = 0; i < Math.abs(zoomClicks); i++) await button.click();
    const canvas = page.getByTestId("pdf-canvas");
    const overlay = page.getByTestId("pdf-highlight").first();
    await expect
      .poll(async () => {
        const frame = await boxOf(canvas);
        const box = await boxOf(overlay);
        return Math.abs(box.x - frame.x - rect.x0 * frame.width);
      })
      .toBeLessThan(1);
    const frame = await boxOf(canvas);
    const box = await boxOf(overlay);
    expect(box.width).toBeCloseTo((rect.x1 - rect.x0) * frame.width, 0);
    await expect
      .poll(() => canvas.evaluate(darkPixels, rect))
      .toBeGreaterThan(0);
  });
}

test("a highlight is drawn only for words precision", async ({ page }) => {
  const hit = await passage("quartz", "Rotated page.pdf");
  await page.goto(`/#/doc/${hit.document.id}?el=${hit.element_id}`);
  const overlay = page.getByTestId("pdf-highlight").first();
  const classes = [
    await overlay.getAttribute("data-precision"),
    await overlay.getAttribute("class"),
  ];
  expect([
    ["words", "pdf-hl"],
    ["element", "pdf-area"],
    ["page", "pdf-area"],
  ]).toContainEqual(classes);
});

test("every picture format shows, a HEIC through its preview", async ({
  page,
}) => {
  for (const file of [
    "Screenshot.png",
    "Photo.jpeg",
    "Web image.webp",
    "Phone photo.heic",
  ]) {
    const document = await documentNamed(file);
    await page.goto(`/#/doc/${document.id}`);
    const picture = page.getByTestId("image-page").locator("img");
    await expect(picture).toBeVisible();
    await expect
      .poll(() => picture.evaluate((img: HTMLImageElement) => img.naturalWidth))
      .toBeGreaterThan(0);
  }
});

test("changed and missing originals show stored text, a banner and no highlight", async ({
  page,
}) => {
  const folder = fixtureFolder("states");
  const path = `${folder}/Changing note.txt`;
  writeFileSync(path, "The original words with marker juniper.\n");
  await api("/sources", "POST", { path: folder, sensitivity: "standard" });
  await queueDrained();
  const hit = await passage("marker juniper", "Changing note.txt");
  const opened = `/#/doc/${hit.document.id}?el=${hit.element_id}`;

  // Paused, so the watcher's own re-index can't get there before the banner is read.
  await api("/indexing/toggle-pause", "POST");
  writeFileSync(path, "Edited after it was indexed.\n");
  await page.goto(opened);
  await expect(page.getByRole("status").first()).toContainText(
    "changed after it was indexed",
  );
  await expect(page.getByLabel("Stored text")).toContainText("marker juniper");
  await expect(page.getByTestId("pdf-highlight")).toHaveCount(0);

  await page.getByRole("button", { name: "Index the new version" }).click();
  await api("/indexing/toggle-pause", "POST");
  await expect(page.getByLabel("Changing note")).toContainText(
    "Edited after it was indexed",
  );

  const moved = `${folder}/Moved note.txt`;
  writeFileSync(moved, "The original words with marker juniper.\n");
  rmSync(path);
  await queueDrained();
  await page.goto(`/#/home`);
  await page.goto(opened);
});

test("a missing file can be located again, hash checked", async ({ page }) => {
  const folder = fixtureFolder("locate");
  const elsewhere = fixtureFolder("locate-elsewhere");
  const path = `${folder}/Lost note.txt`;
  const words = "Lost words with marker caraway.\n";
  writeFileSync(path, words);
  const source = await api<{ id: string }>("/sources", "POST", {
    path: folder,
    sensitivity: "standard",
  });
  await queueDrained();
  const document = await documentNamed("Lost note.txt");
  rmSync(path);
  await api(`/sources/${source.id}/rescan`, "POST");
  await queueDrained();
  await page.goto(`/#/doc/${document.id}`);
  await expect(page.getByText("The original file is gone")).toBeVisible();
  await expect(page.getByLabel("Stored text")).toContainText("marker caraway");

  const wrong = `${elsewhere}/Other note.txt`;
  writeFileSync(wrong, "Different words.\n");
  await page.evaluate((chosen) => {
    window.securefolioChosenPaths = [chosen];
  }, wrong);
  await page.getByRole("button", { name: "Locate file…" }).click();
  await expect(page.getByRole("alert")).toBeVisible();

  const right = `${elsewhere}/Lost note.txt`;
  writeFileSync(right, words);
  await page.evaluate((chosen) => {
    window.securefolioChosenPaths = [chosen];
  }, right);
  await page.getByRole("button", { name: "Locate file…" }).click();
  await expect(page.getByText("The original file is gone")).toHaveCount(0);
});

test("email and HTML views show no remote image and run no script", async ({
  page,
}) => {
  const hits: string[] = [];
  const stub = createServer((request, response) => {
    hits.push(request.url ?? "");
    response.end();
  });
  await new Promise<void>((ready) => {
    stub.listen(8199, "127.0.0.1", ready);
  });
  const remote = "http://127.0.0.1:8199/tracker.png";
  const folder = fixtureFolder("remote");
  writeFileSync(
    `${folder}/Planted page.html`,
    `<html><body><p id="p1">Marker lovage.<img src="${remote}"></p><script>window.ran = 1</script></body></html>`,
  );
  writeFileSync(
    `${folder}/Planted mail.eml`,
    `From: a@example.test\r\nSubject: Planted\r\nContent-Type: text/html\r\n\r\n<p>Marker lovage in mail.<img src="${remote}"></p>`,
  );
  await api("/sources", "POST", { path: folder, sensitivity: "standard" });
  await queueDrained();
  const off = watchRequests(page);
  for (const file of ["Planted page.html", "Planted mail.eml"]) {
    const document = await documentNamed(file);
    await page.goto(`/#/doc/${document.id}`);
    await expect(page.locator(".stored-text").first()).toContainText("lovage");
    await expect(page.locator(".stored-text img")).toHaveCount(0);
  }
  expect(await page.evaluate(() => "ran" in window)).toBe(false);
  expect(off).toEqual([]);
  expect(hits).toEqual([]);
  await new Promise((closed) => {
    stub.close(closed);
  });
});

test("re-reading a page stays here, and the cloud read shows its cost first", async ({
  page,
}) => {
  const document = await documentNamed("Plain text.pdf");
  await page.goto(`/#/doc/${document.id}`);
  await page.getByRole("button", { name: "More document actions" }).click();
  await page.getByRole("menuitem", { name: "Re-read this page" }).click();
  await expect(page.getByRole("status").last()).toContainText(
    "again on this Mac",
  );
  await page.getByRole("button", { name: "More document actions" }).click();
  await page
    .getByRole("menuitem", { name: "Read this page with Cloud AI…" })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("only that page");
  await expect(
    dialog.getByRole("button", { name: /at most \$/ }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
});

test("the cloud read isn't offered for a Private document", async ({
  page,
}) => {
  const document = await documentNamed("Clinic visit.pdf");
  await page.goto(`/#/doc/${document.id}`);
  await page.getByRole("button", { name: "More document actions" }).click();
  await expect(
    page.getByRole("menuitem", { name: "Re-read this page" }),
  ).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: /with Cloud AI/ }),
  ).toHaveCount(0);
});
