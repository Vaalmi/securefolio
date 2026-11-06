import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, refusal } from "../../fakeBackend";
import { installBrowser, open } from "../../testApp";
import { backend, key, sent } from "./keysFixtures";

// Settings, Keys: each state a key's row can be in.

beforeEach(() => {
  installBrowser();
});

describe("Keys", () => {
  it("has a row per provider, showing only the last four characters", async () => {
    backend([
      key("anthropic", { present: true, hint: "••••7Qx2" }),
      key("openai"),
      key("openrouter", { present: true, hint: "••••9dLe" }),
    ]);
    open("/settings/models");
    expect(await screen.findByText("sk-ant-••••7Qx2")).toBeVisible();
    expect(screen.getByText("Workspace ID:", { exact: false })).toBeVisible();
    expect(screen.getAllByText("No key. Optional.")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Add key" })).toBeVisible();
    expect(
      screen.getByText(
        /OpenRouter account's settings decide which companies host/,
      ),
    ).toBeVisible();
    expect(await screen.findByText(/Its model list is from/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeVisible();
  });

  it("tests and saves a new key with its optional ID", async () => {
    backend([key("openai")], {
      "POST /settings/keys/openai/test": key("openai", {
        present: true,
        hint: "••••Rm4c",
      }),
    });
    open("/settings/models");
    await userEvent.click(
      await screen.findByRole("button", { name: "Add key" }),
    );
    await userEvent.type(
      screen.getByLabelText("OpenAI API key"),
      "sk-proj-new",
    );
    await userEvent.type(
      screen.getByLabelText("Project ID (optional)"),
      "proj_7",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Test and save" }),
    );
    expect(
      await screen.findByText(/Key works. Saved to your Keychain./),
    ).toBeVisible();
    expect(sent("/settings/keys/openai/test", "POST")).toEqual({
      key: "sk-proj-new",
      scope_id: "proj_7",
    });
  });

  it("shows a refused key, and gives focus back to Add key on Cancel", async () => {
    backend([key("anthropic")], {
      "POST /settings/keys/anthropic/test": refusal(
        "Anthropic didn't accept this key. Check it and paste it again.",
      ),
    });
    open("/settings/models");
    await userEvent.click(
      await screen.findByRole("button", { name: "Add key" }),
    );
    await userEvent.type(
      screen.getByLabelText("Anthropic API key"),
      "sk-ant-bad",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Test and save" }),
    );
    expect(await screen.findByText(/didn't accept this key/)).toBeVisible();
    expect(screen.getByLabelText("Anthropic API key")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Add key" })).toHaveFocus();
    });
  });

  it("says a new key needs its ID", async () => {
    backend([key("openai")], {
      "POST /settings/keys/openai/test": refusal(
        "This key isn't tied to one project. Add its Project ID, then Test again.",
        422,
      ),
    });
    open("/settings/models");
    await userEvent.click(
      await screen.findByRole("button", { name: "Add key" }),
    );
    await userEvent.type(
      screen.getByLabelText("OpenAI API key"),
      "sk-proj-any",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Test and save" }),
    );
    await waitFor(() => {
      expect(document.getElementById("key-openai-status")).toHaveTextContent(
        "isn't tied to one project",
      );
    });
    expect(screen.getByLabelText("Project ID (optional)")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("keeps a saved key's needs-ID state when Settings is reopened", async () => {
    let record = key("openai", {
      present: true,
      hint: "••••Rm4c",
      needs_id: true,
    });
    backend([], {
      "GET /settings/keys": () => [record],
      // An ID saved but not yet tested: the backend still says it needs one.
      "PUT /settings/keys/openai/scope": (body: { scope_id: string }) => {
        record = { ...record, scope_id: body.scope_id };
        return record;
      },
    });
    open("/settings/models");
    const field = await screen.findByLabelText("Project ID (optional)");
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById("key-openai-status")).toHaveTextContent(
      "This key needs its Project ID. Add it, then Test.",
    );
    await userEvent.type(field, "proj_1");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(sent("/settings/keys/openai/scope", "PUT")).toEqual({
      scope_id: "proj_1",
    });
    await waitFor(() => {
      expect(document.getElementById("key-openai-status")).toHaveTextContent(
        "Project ID saved. Test the key to check it.",
      );
    });
  });

  it("adds an ID, and gives focus back to its button", async () => {
    backend([key("anthropic", { present: true, hint: "••••7Qx2" })], {
      "PUT /settings/keys/anthropic/scope": key("anthropic", {
        present: true,
        scope_id: "wrkspc_1",
      }),
    });
    open("/settings/models");
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Add the Anthropic Workspace ID",
      }),
    );
    await userEvent.type(
      screen.getByLabelText("Workspace ID (optional)"),
      "  wrkspc_1 ",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(sent("/settings/keys/anthropic/scope", "PUT")).toEqual({
      scope_id: "wrkspc_1",
    });
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /the Anthropic Workspace ID/ }),
      ).toHaveFocus();
    });
  });

  it("removes a key only after its confirmation", async () => {
    backend([key("openrouter", { present: true, hint: "••••9dLe" })], {
      "DELETE /settings/keys/openrouter": key("openrouter"),
    });
    open("/settings/models");
    await userEvent.click(
      await screen.findByRole("button", { name: "Remove the OpenRouter key" }),
    );
    expect(screen.getByText(/Remove the OpenRouter key\?/)).toBeVisible();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Remove key" }));
    await waitFor(() => {
      expect(
        calls.some(
          (c) =>
            c.method === "DELETE" && c.path === "/settings/keys/openrouter",
        ),
      ).toBe(true);
    });
  });
});

describe("Keys, when a request fails", () => {
  it("says why saving an ID didn't go, and keeps what was typed", async () => {
    backend([key("anthropic", { present: true, hint: "••••7Qx2" })], {
      "PUT /settings/keys/anthropic/scope": refusal(
        "SecureFolio couldn't save that.",
      ),
    });
    open("/settings/models");
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Add the Anthropic Workspace ID",
      }),
    );
    await userEvent.type(
      screen.getByLabelText("Workspace ID (optional)"),
      "wrkspc_1",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "couldn't save that",
    );
    expect(screen.getByLabelText("Workspace ID (optional)")).toHaveValue(
      "wrkspc_1",
    );
  });

  it("says why a key wasn't removed, and keeps the confirmation", async () => {
    backend([key("openrouter", { present: true, hint: "••••9dLe" })], {
      "DELETE /settings/keys/openrouter": refusal(
        "The Keychain didn't answer.",
      ),
    });
    open("/settings/models");
    await userEvent.click(
      await screen.findByRole("button", { name: "Remove the OpenRouter key" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Remove key" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Keychain didn't answer",
    );
    expect(screen.getByRole("button", { name: "Remove key" })).toBeVisible();
  });
});

describe("Keys, following the backend's record", () => {
  it("follows the backend when a saved ID is the one the key worked with", async () => {
    // The ID put back is the one that worked, so the warning goes.
    let record = key("openai", {
      present: true,
      hint: "••••Rm4c",
      needs_id: true,
    });
    backend([], {
      "GET /settings/keys": () => [record],
      "PUT /settings/keys/openai/scope": (body: { scope_id: string }) => {
        record = { ...record, scope_id: body.scope_id, needs_id: false };
        return record;
      },
    });
    open("/settings/models");
    await userEvent.type(
      await screen.findByLabelText("Project ID (optional)"),
      "proj_right",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(document.getElementById("key-openai-status")).toHaveTextContent(
        /^$/,
      );
    });
    expect(screen.getByText("proj_right")).toBeVisible();
  });

  it("shows the warning when a ready key's ID changes to one not yet tested", async () => {
    // The other way round.
    let record = key("openai", {
      present: true,
      hint: "••••Rm4c",
      scope_id: "proj_right",
    });
    backend([], {
      "GET /settings/keys": () => [record],
      "PUT /settings/keys/openai/scope": (body: { scope_id: string }) => {
        record = { ...record, scope_id: body.scope_id, needs_id: true };
        return record;
      },
    });
    open("/settings/models");
    const change = await screen.findByRole("button", {
      name: "Change the OpenAI Project ID",
    });
    expect(document.getElementById("key-openai-status")).toHaveTextContent(
      /^$/,
    );
    await userEvent.click(change);
    const field = screen.getByLabelText("Project ID (optional)");
    await userEvent.clear(field);
    await userEvent.type(field, "proj_new");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(document.getElementById("key-openai-status")).toHaveTextContent(
        "Project ID saved. Test the key to check it.",
      );
    });
  });

  it("shows a saved key's first needs-ID refusal, and reloads its record", async () => {
    // The record said nothing yet; the test's refusal is the news.
    let record = key("openai", { present: true, hint: "••••Rm4c" });
    backend([], {
      "GET /settings/keys": () => [record],
      "POST /settings/keys/openai/test": () => {
        record = { ...record, needs_id: true };
        return refusal(
          "This key isn't tied to one project. Add its Project ID, then Test again.",
          422,
        )();
      },
    });
    open("/settings/models");
    await userEvent.click(await screen.findByRole("button", { name: "Test" }));
    await waitFor(() => {
      expect(document.getElementById("key-openai-status")).toHaveTextContent(
        "isn't tied to one project",
      );
    });
    expect(screen.getByLabelText("Project ID (optional)")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await waitFor(() => {
      const reloads = calls.filter(
        (c) => c.method === "GET" && c.path === "/settings/keys",
      );
      expect(reloads.length).toBeGreaterThan(1);
    });
    expect(document.getElementById("key-openai-status")).toHaveTextContent(
      "isn't tied to one project",
    );
  });

  it("drops a typed ID's refusal on Cancel, back to the saved ID's record", async () => {
    // The saved proj_right still works; only the typed one was refused.
    const record = key("openai", {
      present: true,
      hint: "••••Rm4c",
      scope_id: "proj_right",
    });
    backend([], {
      "GET /settings/keys": () => [record],
      "POST /settings/keys/openai/test": refusal(
        "This key isn't tied to one project. Add its Project ID, then Test again.",
        422,
      ),
    });
    open("/settings/models");
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Change the OpenAI Project ID",
      }),
    );
    const field = screen.getByLabelText("Project ID (optional)");
    await userEvent.clear(field);
    await userEvent.type(field, "proj_wrong");
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => {
      expect(document.getElementById("key-openai-status")).toHaveTextContent(
        "isn't tied to one project",
      );
    });
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(document.getElementById("key-openai-status")).toHaveTextContent(
      /^$/,
    );
    expect(screen.getByText("proj_right")).toBeVisible();
  });
});
