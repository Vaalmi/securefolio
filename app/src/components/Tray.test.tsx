import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, fakeBackend, refusal } from "../fakeBackend";
import { installBrowser, open, posted } from "../testApp";

beforeEach(() => {
  installBrowser();
});

describe("the tray", () => {
  const row = (
    status: string,
    skip: string | null = null,
    jobId: string | null = "j1",
  ) => ({
    id: `${status}-${String(skip)}`,
    job_id: jobId,
    document_id: skip ?? "d1",
    file: `${skip ?? status}.pdf`,
    folder: "Home",
    status,
    detail: `${status} detail`,
    skip_kind: skip,
  });

  it("acts on each kind of row", async () => {
    fakeBackend({
      "GET /jobs": [
        row("Queued"),
        row("Failed"),
        row("Completed"),
        row("Skipped", "password", null),
        row("Skipped", "cloud", null),
      ],
    });
    open("/home?tray=all");
    const tray = await screen.findByRole("region", { name: "Import tray" });
    expect(await within(tray).findByText("Indexing 3 of 9")).toBeVisible();
    await userEvent.click(
      await within(tray).findByRole("button", { name: "Cancel Queued.pdf" }),
    );
    await waitFor(() => {
      expect(posted("/jobs/j1/cancel")).toBe(true);
    });
    await userEvent.click(
      within(tray).getByRole("button", { name: "Retry Failed.pdf" }),
    );
    await waitFor(() => {
      expect(posted("/jobs/j1/retry")).toBe(true);
    });
    await userEvent.click(
      within(tray).getByRole("button", {
        name: "Download & Index cloud.pdf",
      }),
    );
    await waitFor(() => {
      expect(posted("/documents/cloud/download")).toBe(true);
    });
    await userEvent.click(
      within(tray).getByRole("button", { name: "Unlock & Index password.pdf" }),
    );
    await userEvent.type(
      within(tray).getByLabelText("Password for password.pdf"),
      "pw",
    );
    await userEvent.click(
      within(tray).getByRole("button", { name: "Unlock & Index" }),
    );
    await waitFor(() => {
      const sent = calls.find((c) => c.path === "/documents/password/unlock");
      expect(sent?.body).toEqual({ password: "pw" });
    });
    await userEvent.click(within(tray).getByRole("tab", { name: /Failed/ }));
    await waitFor(() => {
      expect(window.location.hash).toContain("tray=failed");
    });
    await userEvent.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Import tray" })).toBeNull();
    });
  });

  it("says when there's nothing, and shows a refusal", async () => {
    fakeBackend();
    const first = open("/home?tray=active");
    expect(await screen.findByText("Nothing here.")).toBeVisible();
    first.unmount();
    fakeBackend({ "GET /jobs": refusal("Down.", 500) });
    open("/library?tray=all");
    expect((await screen.findAllByText("Down.")).length).toBeGreaterThan(0);
  });
});

describe("importing", () => {
  it("copies what was chosen, at the chosen sensitivity", async () => {
    window.securefolioChosenPaths = [
      "/a/One.pdf",
      "/a/Two.pdf",
      "/a/Three.pdf",
      "/a/Four.pdf",
    ];
    fakeBackend({ "POST /imports": [] });
    open("/library?import=1");
    const dialog = await screen.findByRole("dialog", { name: "Import files" });
    await userEvent.click(
      within(dialog).getByRole("button", { name: /Choose files/ }),
    );
    expect(
      await within(dialog).findByText(/4 chosen: One.pdf, Two.pdf, Three.pdf…/),
    ).toBeVisible();
    await userEvent.click(
      within(dialog).getByRole("radio", { name: /Private/ }),
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: /Import 4 files/ }),
    );
    await waitFor(() => {
      const sent = calls.find((c) => c.path === "/imports");
      expect(sent?.body).toMatchObject({ sensitivity: "private" });
    });
    delete window.securefolioChosenPaths;
  });

  it("copies a drop in and opens the tray", async () => {
    fakeBackend({ "POST /imports": [] });
    open("/library");
    await screen.findByRole("heading", { level: 1 });
    await waitFor(() => {
      window.dispatchEvent(
        new CustomEvent("securefolio-drop", { detail: ["/a/One.pdf"] }),
      );
      expect(calls.some((c) => c.path === "/imports")).toBe(true);
    });
    await waitFor(() => {
      expect(window.location.hash).toContain("tray=active");
    });
  });

  it("binds the shortcuts", async () => {
    fakeBackend();
    open("/library");
    await screen.findByRole("heading", { level: 1 });
    await userEvent.keyboard("{Control>}o{/Control}");
    expect(
      await screen.findByRole("dialog", { name: "Import files" }),
    ).toBeVisible();
    await userEvent.keyboard("{Escape}");
    await userEvent.keyboard("{Control>}{Shift>}i{/Shift}{/Control}");
    expect(
      await screen.findByRole("region", { name: "Import tray" }),
    ).toBeVisible();
    await userEvent.keyboard("{Control>}f{/Control}");
    await waitFor(() => {
      expect(window.location.hash).toContain("/home");
    });
  });
});

describe("a long tray", () => {
  it("asks for more until every row is reachable", async () => {
    fakeBackend({
      "GET /jobs/counts": {
        done: 0,
        total: 0,
        active: 0,
        completed: 150,
        failed: 0,
        skipped: 0,
        latest: 1,
      },
    });
    open("/home?tray=completed");
    await userEvent.click(
      await screen.findByRole("button", { name: "Show more" }),
    );
    await waitFor(() => {
      expect(calls.some((c) => c.path.includes("limit=200"))).toBe(true);
    });
  });
});
