import { rmSync, writeFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import {
  api,
  documentNamed,
  FIXTURES,
  fixtureFolder,
  queueDrained,
} from "./helpers";

test("a drop makes one import per file, including a saved page", async ({
  page,
}) => {
  const folder = fixtureFolder("drop");
  const paths: string[] = [];
  for (let i = 0; i < 49; i++) {
    const path = `${folder}/Dropped ${String(i)}.txt`;
    writeFileSync(path, `Dropped note ${String(i)} with marker cassia.\n`);
    paths.push(path);
  }
  const saved = `${folder}/Dropped page.html`;
  writeFileSync(
    saved,
    "<html><body><p>Marker cassia on a page.</p></body></html>",
  );
  paths.push(saved);
  const before = await api<{ total: number }>("/library/counts");
  await page.goto("/#/library");
  await expect(page.getByRole("grid")).toBeVisible();
  await page.evaluate((dropped) => {
    window.dispatchEvent(
      new CustomEvent("securefolio-drop", { detail: dropped }),
    );
  }, paths);
  await expect(page.getByRole("region", { name: "Import tray" })).toBeVisible();
  await queueDrained();
  const after = await api<{ total: number }>("/library/counts");
  expect(after.total - before.total).toBe(50);
  const copy = await documentNamed("Dropped page.html");
  expect(copy.status).toBe("indexed");
  await expect(page).toHaveURL(/tray=active/);
});

test("Unlock & Index takes a password and clears the row", async ({ page }) => {
  await page.goto("/#/home?tray=skipped");
  const tray = page.getByRole("region", { name: "Import tray" });
  await expect(tray.getByText("Locked statement.pdf")).toBeVisible();
  await tray
    .getByRole("button", { name: "Unlock & Index Locked statement.pdf" })
    .click();
  await tray
    .getByLabel("Password for Locked statement.pdf")
    .fill("fixture-pass");
  await tray
    .getByRole("button", { name: "Unlock & Index", exact: true })
    .click();
  await queueDrained();
  await expect(tray.getByText("Locked statement.pdf")).toHaveCount(0);
  const unlocked = await documentNamed("Locked statement.pdf");
  expect(unlocked.status).toBe("indexed");
});

test("the wrong password returns the file to Skipped with a reason", async ({
  page,
}) => {
  await page.goto("/#/home?tray=skipped");
  const tray = page.getByRole("region", { name: "Import tray" });
  await tray
    .getByRole("button", { name: "Unlock & Index Locked memo.docx" })
    .click();
  await tray.getByLabel("Password for Locked memo.docx").fill("not-it");
  await tray
    .getByRole("button", { name: "Unlock & Index", exact: true })
    .click();
  await queueDrained();
  await expect(tray.getByText("didn't open this file")).toBeVisible();
});

test("Skipped lists what a scan marked with no job, with its reason", async ({
  page,
}) => {
  await page.goto("/#/home?tray=skipped");
  const tray = page.getByRole("region", { name: "Import tray" });
  await expect(tray.getByText("Unknown format.xyz")).toBeVisible();
  await expect(tray.getByText("In the cloud.pdf")).toBeVisible();
  await expect(
    tray.getByRole("button", { name: "Download & Index In the cloud.pdf" }),
  ).toBeVisible();
});

test("the indexing line reads from the queue, and Cancel stops a job", async ({
  page,
}) => {
  const folder = fixtureFolder("cancel");
  const slow = `${FIXTURES}/slow`;
  writeFileSync(slow, "");
  for (let i = 0; i < 300; i++) {
    writeFileSync(
      `${folder}/Queued ${String(i)}.txt`,
      `Queued ${String(i)}.\n`,
    );
  }
  await page.goto("/#/library?tray=active");
  const tray = page.getByRole("region", { name: "Import tray" });
  await api("/sources", "POST", { path: folder, sensitivity: "standard" });
  await expect(tray.getByRole("status")).toContainText(/Indexing \d+ of \d+/, {
    timeout: 30_000,
  });
  const cancel = tray.getByRole("button", { name: /^Cancel Queued/ }).last();
  await cancel.click();
  await expect(page.getByText("Nothing was left half-indexed")).toBeVisible();
  rmSync(slow);
  await queueDrained();
  const { rows } = await api<{ rows: { status: string }[] }>(
    "/library?name=Queued&limit=400",
  );
  expect(rows.some((row) => row.status === "cancelled")).toBe(true);
});

test("a failed job can be retried", async ({ page }) => {
  const folder = fixtureFolder("failing");
  writeFileSync(`${folder}/Broken file.pdf`, "%PDF-1.7 this isn't a real PDF");
  await api("/sources", "POST", { path: folder, sensitivity: "standard" });
  await queueDrained();
  await page.goto("/#/home?tray=failed");
  const tray = page.getByRole("region", { name: "Import tray" });
  const retry = tray.getByRole("button", { name: "Retry Broken file.pdf" });
  await expect(retry).toBeVisible();
  await retry.click();
  await queueDrained();
  await expect(retry).toBeVisible();
});
