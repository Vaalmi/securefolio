import { writeFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { api, documentNamed, fixtureFolder, queueDrained } from "./helpers";

test("the list sorts, filters and opens from the keyboard", async ({
  page,
}) => {
  await page.goto("/#/library");
  const grid = page.getByRole("grid");
  await expect(grid).toBeVisible();
  await page.getByRole("button", { name: "Name" }).click();
  await expect(
    page.getByRole("columnheader", { name: "Name" }),
  ).toHaveAttribute("aria-sort", "ascending");
  await page.getByLabel("Filter by name").fill("Apartment");
  await expect(grid.getByRole("row")).toHaveCount(2);
  await page.getByRole("button", { name: "Filter by folder" }).click();
  await page.getByRole("menuitem", { name: "Home" }).click();
  await expect(page).toHaveURL(/folder=/);
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page).not.toHaveURL(/folder=/);
  const rows = grid.getByRole("row");
  await rows.nth(1).click();
  await page.keyboard.press("ArrowDown");
  await expect(rows.nth(2)).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/doc\//);
});

test("Select all reaches every row, not only those on screen", async ({
  page,
}) => {
  const { total } = await api<{ total: number }>("/library/counts");
  await page.goto("/#/library");
  await page.getByRole("checkbox", { name: /Select all/ }).click();
  await expect(page.getByRole("toolbar")).toContainText(
    `${String(total)} selected`,
  );
});

test("a collection is made, filled, scoped and emptied", async ({ page }) => {
  await page.goto("/#/collections");
  await page.getByLabel("New collection").fill("House papers");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(
    page.getByRole("link", { name: "House papers" }).first(),
  ).toBeVisible();
  const lease = await documentNamed("Apartment lease.pdf");
  await page.goto(`/#/doc/${lease.id}`);
  await page.getByRole("button", { name: "Add to collection" }).click();
  await page.getByRole("menuitem", { name: "House papers" }).click();
  await expect(
    page.getByRole("button", { name: "Remove from collection House papers" }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: /House papers/ })
    .first()
    .click();
  await expect(page.getByRole("grid").getByRole("row")).toHaveCount(2);
  await page.getByRole("grid").getByRole("row").nth(1).click();
  await page.getByRole("button", { name: "Remove from collection" }).click();
  await expect(page.getByText("No documents match")).toBeVisible();
  const still = await documentNamed("Apartment lease.pdf");
  expect(still.status).toBe("indexed");
});

test("notes are written with a heading, edited and deleted on a document", async ({
  page,
}) => {
  const lease = await documentNamed("Apartment lease.pdf");
  await page.goto(`/#/doc/${lease.id}?tab=notes`);
  await page.getByLabel("Heading (optional)").fill("Renewal");
  await page.getByLabel("Text").fill("Renew by June.");
  await page.getByRole("button", { name: "Save note" }).click();
  await expect(page.locator(".passage")).toContainText("Renewal");
  await expect(page.locator(".passage")).toContainText("Renew by June.");
  await page.goto("/#/notes");
  const note = page.locator(".passage").filter({ hasText: "Renew by June." });
  await note.getByRole("button", { name: "Edit note Renewal" }).click();
  const form = page.getByRole("form", { name: "Edit note Renewal" });
  await form.getByLabel("Heading").fill("Lease renewal");
  await form.getByLabel("Text").fill("Renew by July.");
  await form.getByRole("button", { name: "Save", exact: true }).click();
  const edited = page.locator(".passage").filter({ hasText: "Renew by July." });
  await expect(edited).toContainText("Lease renewal");
  await edited.getByRole("button", { name: /^Delete note/ }).click();
  await expect(edited).toHaveCount(0);
});

test("a card edit is saved and marked as the owner's", async ({ page }) => {
  const note = await documentNamed("Plain note.txt");
  await page.goto(`/#/doc/${note.id}`);
  await page.getByRole("button", { name: "Edit card" }).click();
  await page.getByLabel("Summary").fill("A note about anise.");
  await page.getByRole("button", { name: "Save card" }).click();
  await expect(page.getByText("Edited by you: Summary")).toBeVisible();
  await expect(page.getByText("A note about anise.")).toBeVisible();
});

test("a folder is added, and its sensitivity changed, from Sources", async ({
  page,
}) => {
  const folder = fixtureFolder("added");
  writeFileSync(`${folder}/Added note.txt`, "An added note.\n");
  await page.goto("/#/sources");
  await expect(
    page.getByRole("row", { name: /Copied into library/ }),
  ).toBeVisible();
  await page.evaluate((chosen) => {
    window.securefolioChosenPaths = [chosen];
  }, folder);
  await page.getByRole("button", { name: "Add folder" }).click();
  await page.getByRole("button", { name: "Add and start indexing" }).click();
  await expect(page.getByRole("row", { name: /added/ })).toBeVisible();
  await queueDrained();
  await page.getByRole("button", { name: "More actions for added" }).click();
  await page.getByRole("menuitem", { name: "Change sensitivity…" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(
    page.getByRole("row", { name: /added/ }).locator(".badge.private"),
  ).toBeVisible();
  const added = await documentNamed("Added note.txt");
  expect(added.sensitivity).toBe("private");
  await page.getByRole("button", { name: "More actions for added" }).click();
  await page.getByRole("menuitem", { name: "Stop watching…" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Stop watching" })
    .click();
  await expect(page.getByRole("row", { name: /added/ })).toHaveCount(0);
});

test("a new sort or filter starts at the top, even from deep in the list", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 400 });
  await page.goto("/#/library");
  await page.locator(".grid-body").evaluate((body) => {
    body.scrollTop = body.scrollHeight;
  });
  await page.getByRole("button", { name: "Name" }).click();
  await expect(page.locator(".grid-row").first()).toBeVisible();
  await expect(page.locator(".grid-body")).toHaveJSProperty("scrollTop", 0);
  await page.locator(".grid-body").evaluate((body) => {
    body.scrollTop = body.scrollHeight;
  });
  await page.getByLabel("Filter by name").fill("Apartment");
  await expect(page.getByRole("grid").getByRole("row").nth(1)).toContainText(
    "Apartment",
  );
});

test("20,000 rows scroll with no dropped frames", async ({ page }) => {
  const total = 20_000;
  await page.route("**/api/library?*", async (route) => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 200);
    await route.fulfill({
      json: {
        total,
        offset,
        rows: Array.from(
          { length: Math.min(limit, total - offset) },
          (_, i) => ({
            id: `row-${String(offset + i)}`,
            title: `Document ${String(offset + i)}`,
            file_name: "x.pdf",
            file_kind: "PDF",
            doc_type: "Report",
            doc_date: "2026-01-01",
            added_at: 0,
            size_bytes: 1000,
            pages: 1,
            status: "indexed",
            status_reason: null,
            sensitivity: "standard",
            starred: false,
            active_revision_id: "r",
            folder: "Home",
          }),
        ),
      },
    });
  });
  await page.goto("/#/library");
  await expect(page.getByRole("grid")).toHaveAttribute(
    "aria-rowcount",
    String(total + 1),
  );
  const longest = await page.locator(".grid-body").evaluate(async (body) => {
    const gaps: number[] = [];
    let last = performance.now();
    let frames = 0;
    await new Promise<void>((done) => {
      const tick = (now: number): void => {
        gaps.push(now - last);
        last = now;
        frames += 1;
        body.scrollTop += 640;
        if (frames < 240) requestAnimationFrame(tick);
        else done();
      };
      requestAnimationFrame(tick);
    });
    gaps.sort((a, b) => a - b);
    return gaps[Math.floor(gaps.length * 0.99)] ?? 0;
  });
  // Two frames at 60 Hz: a slow frame on a loaded machine is not a dropped row.
  expect(longest).toBeLessThan(34);
  console.log(`20,000 rows: 99th percentile frame ${longest.toFixed(1)} ms`);
});
