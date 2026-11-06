import { expect, test } from "@playwright/test";
import type { Locator } from "@playwright/test";

import { api, documentNamed, passage } from "./helpers";

function placeOf(element: Locator): Promise<{ left: number; width: number }> {
  return element.evaluate((e) => {
    const { left, width } = e.getBoundingClientRect();
    return { left, width };
  });
}

test("journey 3: Command-Return from Home asks, and a citation opens beside the chat", async ({
  page,
}) => {
  await page.goto("/#/home");
  const box = page.getByRole("combobox", {
    name: "Search or ask your library",
  });
  await box.fill("What does the roof report recommend?");
  // The Ask button waits for who would answer; then Return asks.
  await expect(
    page.getByRole("button", { name: /^Ask (Cloud|Local) AI/ }),
  ).toBeEnabled();
  await box.press("Enter");
  await expect(page).toHaveURL(/#\/ask\/[0-9a-f]{32}/);
  const answer = page.getByRole("article", { name: "Answer" });
  await expect(answer).toBeVisible();
  await expect(answer.locator(".answer-meta")).toContainText("cited");
  await answer.locator("button.cite").first().click();
  await expect(page.getByRole("heading", { name: "Preview" })).toBeVisible();
  await page.getByRole("button", { name: "Open in viewer" }).click();
  await expect(page).toHaveURL(/#\/doc\/.*el=/);
});

test("journey 4: three reports, a follow-up, a note, and it all survives a reload", async ({
  page,
}) => {
  const ids = await Promise.all(
    [
      "Roof inspection report.pdf",
      "Energy audit report.pdf",
      "Foundation survey report.pdf",
    ].map(async (file) => (await documentNamed(file)).id),
  );
  await page.goto(`/#/ask/new?scope=sel:${ids.join(",")}`);
  await expect(
    page.getByRole("heading", { name: /these 3 documents/ }),
  ).toBeVisible();
  const dock = page.getByRole("textbox", { name: "Ask a question" });
  await dock.fill("Compare what these recommend");
  // The Ask button waits for who would answer; then Return asks.
  await expect(
    page.getByRole("button", { name: /^Ask (Cloud|Local) AI/ }),
  ).toBeEnabled();
  await dock.press("Enter");
  const answer = page.getByRole("article", { name: "Answer" });
  await expect(answer).toBeVisible();
  await expect(answer.locator("button.cite")).toHaveCount(3);
  const followUp = page.getByRole("textbox", { name: "Ask a follow-up" });
  await followUp.fill("What about the second one?");
  await followUp.press("Enter");
  await expect(page.getByRole("article", { name: "Answer" })).toHaveCount(2);
  await page.getByRole("button", { name: "Save as note" }).first().click();
  await expect(
    page.getByRole("button", { name: "Saved as note" }),
  ).toBeVisible();
  const chat = page.url();
  await page.reload();
  await expect(page.getByRole("article", { name: "Answer" })).toHaveCount(2);
  expect(page.url()).toBe(chat);
  await page.goto("/#/notes");
  await page
    .getByRole("button", { name: /^Citation 1/ })
    .first()
    .click();
  await expect(page).toHaveURL(/#\/doc\/.*el=/);
});

test("back from the viewer, a long chat is at its newest answer, and the page shows its left edge", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 560 });
  await page.goto("/#/ask/new?scope=all");
  const dock = page.getByRole("textbox", { name: "Ask a question" });
  await dock.fill("What does the roof report recommend?");
  await expect(
    page.getByRole("button", { name: /^Ask (Cloud|Local) AI/ }),
  ).toBeEnabled();
  await dock.press("Enter");
  await expect(page).toHaveURL(/#\/ask\/[0-9a-f]{32}/);
  const answers = page.getByRole("article", { name: "Answer" });
  await expect(answers).toHaveCount(1);
  const followUp = page.getByRole("textbox", { name: "Ask a follow-up" });
  for (const [i, question] of [
    "And the energy audit?",
    "And the foundation survey?",
    "Which is most urgent?",
  ].entries()) {
    await followUp.fill(question);
    await expect(
      page.getByRole("button", { name: /^Ask (Cloud|Local) AI/ }),
    ).toBeEnabled();
    await followUp.press("Enter");
    await expect(answers).toHaveCount(i + 2);
  }
  await expect(answers.last()).toBeInViewport();
  // Two citations from the first answer, each opened from the chat.
  const chips = answers.first().locator("button.cite");
  await chips.nth(0).click();
  await chips.nth(1).click();
  expect(new URL(page.url()).hash.match(/panel=1/g)).toHaveLength(1);
  await page.getByRole("button", { name: "Open in viewer" }).click();
  await expect(page).toHaveURL(/#\/doc\/.*el=/);
  // A page wider than the pane starts at the pane's left edge, not to the left of it.
  const sheet = page.getByTestId("pdf-page");
  const pane = page.locator(".viewer .zoom-wrap");
  const { width: room, left: start } = await placeOf(pane);
  // Drawn at 100% beside the side panel, the page is wider than its pane.
  await expect
    .poll(async () => (await placeOf(sheet)).width)
    .toBeGreaterThan(room);
  expect((await placeOf(sheet)).left).toBeGreaterThanOrEqual(start);
  await page.getByRole("button", { name: "Back" }).click();
  await expect(answers).toHaveCount(4);
  await expect(answers.last()).toBeInViewport();
});

test("the three Sources tabs show what was cited, searched and done", async ({
  page,
}) => {
  await page.goto(
    "/#/ask/new?q=What%20does%20the%20roof%20report%20recommend%3F",
  );
  await expect(page.getByRole("article", { name: "Answer" })).toBeVisible();
  await expect(page.getByRole("tab", { name: /Cited/ })).toBeVisible();
  await page.getByRole("tab", { name: /Also searched/ }).click();
  await expect(page.locator("#src-panel")).toContainText("Searched, not used");
  await page.getByRole("tab", { name: "Activity" }).click();
  await expect(page.locator("#src-panel")).toContainText(
    "Wrote the answer with",
  );
});

test("a Private chat answers on this Mac, with the badge, and sends nothing", async ({
  page,
}) => {
  await page.goto("/#/ask/new?scope=all-private");
  const dock = page.getByRole("textbox", { name: "Ask a question" });
  await dock.fill("What does the clinic visit say?");
  // The Ask button waits for who would answer; then Return asks.
  await expect(
    page.getByRole("button", { name: /^Ask (Cloud|Local) AI/ }),
  ).toBeEnabled();
  await dock.press("Enter");
  const answer = page.getByRole("article", { name: "Answer" });
  await expect(answer).toBeVisible();
  await expect(answer.locator(".badge.local")).toBeVisible();
  await expect(page.locator(".badge.private").first()).toBeVisible();
  await expect(
    page.getByText("Nothing in this chat leaves your Mac"),
  ).toBeVisible();
});

test("Ask this document, from the viewer", async ({ page }) => {
  const lease = await documentNamed("Apartment lease.pdf");
  await page.goto(`/#/doc/${lease.id}`);
  await page.getByRole("tab", { name: "Ask" }).click();
  await page.getByLabel("Ask this document").fill("Who signs it?");
  await page.getByRole("button", { name: /^Ask Cloud AI/ }).click();
  await expect(page.getByRole("article", { name: "Answer" })).toBeVisible();
  const threads =
    await api<{ scope: { kind: string; ids?: string[] } }[]>("/threads");
  expect(threads.at(0)?.scope.ids).toEqual([lease.id]);
});

test("a collection and a filter become fixed scopes", async ({ page }) => {
  const made = await api<{ id: string }>("/collections", "POST", {
    name: "Ask me",
  });
  const lease = await documentNamed("Apartment lease.pdf");
  await api(`/collections/${made.id}/add`, "POST", {
    document_ids: [lease.id],
  });
  await page.goto(`/#/collections/${made.id}`);
  await page.getByRole("button", { name: "Ask this collection" }).click();
  await expect(
    page.getByRole("button", { name: /Collection: Ask me/ }),
  ).toBeVisible();
  await page.goto("/#/library");
  await page.getByLabel("Filter by name").fill("Household notice");
  await page.getByRole("button", { name: /Ask about these/ }).click();
  await expect(page).toHaveURL(/#\/ask\/[0-9a-f]{32}/);
  await expect(
    page.getByRole("button", { name: /Search: Household notice/ }),
  ).toBeVisible();
});

test("All chats lists them, and a chat can be renamed and deleted", async ({
  page,
}) => {
  const thread = await api<{ id: string }>("/threads", "POST", {});
  await page.goto(`/#/ask/${thread.id}`);
  await page.getByRole("button", { name: "Chat actions" }).click();
  await page.getByRole("menuitem", { name: "Rename" }).click();
  await page.getByLabel("Chat name").fill("Renamed chat");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(
    page.getByRole("heading", { name: "Renamed chat" }),
  ).toBeVisible();
  await page.goto("/#/chats");
  await expect(page.locator("main").getByText("Renamed chat")).toBeVisible();
  await page.goto(`/#/ask/${thread.id}`);
  await page.getByRole("button", { name: "Chat actions" }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await expect(page).toHaveURL(/#\/ask\/new/);
});

test("Memory settings list, and a planted memory is deleted", async ({
  page,
}) => {
  await page.goto("/#/settings/memory");
  await expect(page.getByRole("switch", { name: "Memory" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /What SecureFolio remembers/ }),
  ).toBeVisible();
});

test("the viewer's citation still works from a passage", async ({ page }) => {
  const hit = await passage("marker clove", "Moving checklist.md");
  await page.goto(`/#/doc/${hit.document.id}?el=${hit.element_id}`);
  await expect(page.locator("#cited-passage")).toContainText("marker clove");
});

test("a chat's row menu shows in full at the bottom of a long sidebar", async ({
  page,
}) => {
  const made = await Promise.all(
    Array.from({ length: 14 }, async (_, n) => {
      const { id } = await api<{ id: string }>("/threads", "POST", {});
      await api(`/threads/${id}`, "PATCH", {
        pinned: true,
        title: `Row ${String(n)}`,
      });
      return id;
    }),
  );
  await page.goto("/#/chats");
  const nav = page.getByRole("navigation", { name: "SecureFolio" });
  const menus = nav.getByRole("button", { name: /^Actions for Row/ });
  await expect(menus).toHaveCount(14);
  await menus.last().scrollIntoViewIfNeeded();
  await menus.last().locator("..").locator("..").hover();
  await menus.last().click();
  for (const item of ["Rename", "Pin", "Unpin", "Delete"]) {
    const row = page.getByRole("menuitem", { name: item, exact: true });
    if ((await row.count()) > 0) await expect(row).toBeInViewport({ ratio: 1 });
  }
  await page.keyboard.press("Escape");
  await Promise.all(made.map((id) => api(`/threads/${id}`, "DELETE")));
});
