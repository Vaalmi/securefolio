import { expect, test } from "@playwright/test";

import { api, documentNamed, watchRequests } from "./helpers";

test("the app reaches the backend, and no request leaves the machine", async ({
  page,
}) => {
  const off = watchRequests(page);
  await page.goto("/#/home");
  await expect(page.getByRole("heading", { name: "Recents" })).toBeVisible();
  expect(off).toEqual([]);
});

test("results appear as you type, in two groups, and Return opens one", async ({
  page,
}) => {
  await page.goto("/#/home");
  const box = page.getByRole("combobox", {
    name: "Search or ask your library",
  });
  await box.fill("Apartment");
  const results = page.getByRole("listbox", { name: "Search results" });
  await expect(
    results.getByRole("group", { name: /^Documents/ }),
  ).toContainText("Apartment lease");
  await box.fill("marker clove");
  await expect(results.getByRole("group", { name: /^Passages/ })).toContainText(
    "marker clove",
  );
  await box.press("ArrowDown");
  await box.fill("Apartment lease");
  await expect(results).toContainText("Apartment lease");
  // Typing again after the arrows: Command-Return still opens the first match.
  await box.press("ControlOrMeta+Enter");
  await expect(page).toHaveURL(/#\/doc\//);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Apartment lease",
  );
});

test("Private stays out of the default scope and in when it's chosen", async ({
  page,
}) => {
  await page.goto("/#/home");
  const box = page.getByRole("combobox", {
    name: "Search or ask your library",
  });
  await box.fill("Clinic visit");
  const results = page.locator(".results");
  await expect(results).toContainText("1 Private document matches");
  await expect(results.getByRole("option")).not.toContainText(["Clinic visit"]);
  await page.getByRole("button", { name: /^Scope:/ }).click();
  await page.getByRole("menuitemradio", { name: /Private included/ }).click();
  await expect(results).toContainText("Clinic visit");
  await expect(results.locator(".badge.private").first()).toBeVisible();
});

test("journey 3's keys: Return asks, and after the arrows Return opens", async ({
  page,
}) => {
  await page.goto("/#/home");
  const box = page.getByRole("combobox", {
    name: "Search or ask your library",
  });
  await box.fill("lease");
  const first = page.getByRole("option").first();
  await expect(first).toBeVisible();
  await expect(first).toHaveAttribute("aria-selected", "false");
  await expect(first.locator(".r-open")).toHaveText("⌘↵");
  await page.keyboard.press("ArrowDown");
  await expect(first).toHaveAttribute("aria-selected", "true");
  await expect(first.locator(".r-open")).toHaveText("↵");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/doc\//);
  await page.goto("/#/home");
  await box.fill("What does the lease say about pets?");
  await expect(
    page.getByRole("button", { name: /^Ask Cloud AI/ }),
  ).toBeEnabled();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/ask\/(new|[0-9a-f]{32})/);
});

test("the Ask button's label comes from the backend", async ({ page }) => {
  const asked: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/ask/route")) asked.push(request.url());
  });
  await page.goto("/#/home");
  await expect(
    page.getByRole("button", { name: /^Ask Cloud AI/ }),
  ).toBeVisible();
  await page.getByRole("radio", { name: "Private" }).click();
  await expect(
    page.getByRole("button", { name: /Ask Local AI/ }),
  ).toBeVisible();
  expect(asked.some((url) => url.includes("mode=private"))).toBe(true);
});

test("saved links land on a document even with no search", async ({ page }) => {
  const document = await documentNamed("Apartment lease.pdf");
  await page.goto(`/#/doc/${document.id}`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Apartment lease",
  );
  const recents = await api<{ rows: { id: string }[] }>(
    "/library?view=recents",
  );
  await expect
    .poll(async () => {
      const fresh = await api<{ rows: { id: string }[] }>(
        "/library?view=recents",
      );
      return fresh.rows.at(0)?.id;
    })
    .toBe(document.id);
  expect(recents.rows).toBeDefined();
});
