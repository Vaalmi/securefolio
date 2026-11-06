import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, fakeBackend, refusal } from "../fakeBackend";
import { installBrowser, open, posted } from "../testApp";

beforeEach(() => {
  installBrowser();
});

const SETTINGS = {
  setup_done: true,
  model_answer: "anthropic:claude-sonnet-5-5",
  model_deep: "anthropic:claude-opus-5-5",
  model_helper: "anthropic:claude-haiku-5-5",
  model_page: "anthropic:claude-sonnet-5-5",
  local_answers: "gemma4:26b",
  local_cards: "gemma4:26b",
  local_figures: "gemma4:26b",
  default_sensitivity: "standard",
  spend_cap_usd: 25,
  backup_nightly: true,
  backup_folder: null,
  offline: false,
};
const GROUP = {
  state: "installed",
  bytes_done: 9,
  bytes_total: 9,
  reason: null,
};
const GEMMA = {
  name: "gemma4:26b",
  size: 16146867483,
  text: true,
  images: true,
  jobs: ["local_answers", "local_cards", "local_figures"],
  memory: { gb: 15, about: true },
};
const MODELS = {
  search: GROUP,
  page: GROUP,
  jobs: {
    local_answers: { tag: "gemma4:26b", problem: null },
    local_cards: { tag: "gemma4:26b", problem: null },
    local_figures: { tag: "gemma4:26b", problem: null },
  },
  installed: { local: [GEMMA], remote: [] },
  memory: { gb: 15, about: true },
  mac_memory_gb: 48,
  local: {
    tag: "gemma4:26b",
    recommended: "gemma4:26b",
    installed: true,
    pull: { state: "not_installed", done: 0, total: 0, reason: null },
  },
  ollama: {
    running: true,
    tags: ["gemma4:26b"],
    refused: null,
    no_cloud: true,
  },
};

function sent(path: string, method = "PATCH") {
  return calls.filter((c) => c.path === path && c.method === method).at(-1)
    ?.body;
}

const KEYS = [
  {
    provider: "anthropic",
    name: "Anthropic",
    present: true,
    hint: "••••7Qx2",
    id_label: "Workspace ID",
    scope_id: null,
    needs_id: false,
  },
];

const CLAUDE = (name: string, label: string) => ({
  id: `anthropic:${name}`,
  name: label,
  provider: "anthropic",
  input_usd: 2,
  output_usd: 10,
  measured: true,
  listed: true,
});
const TASK_MODELS = {
  providers: [{ id: "anthropic", name: "Anthropic", key_ready: true }],
  tasks: [
    {
      task: "answer",
      field: "model_answer",
      chosen: "anthropic:claude-sonnet-5-5",
      may_run_locally: true,
      models: [
        CLAUDE("claude-sonnet-5-5", "Claude Sonnet 5.5"),
        CLAUDE("claude-opus-5-5", "Claude Opus 5.5"),
      ],
    },
  ],
};

describe("key and models", () => {
  it("shows the keys, and saves a model", async () => {
    fakeBackend({
      "GET /settings": SETTINGS,
      "GET /settings/keys": KEYS,
      "GET /models/tasks": TASK_MODELS,
      "PATCH /settings": {},
    });
    open("/settings/models");
    expect(await screen.findByText("sk-ant-••••7Qx2")).toBeVisible();
    // The Private and Offline row names the answers' local model.
    expect(await screen.findByText("Local model (gemma4:26b)")).toBeVisible();
    await userEvent.selectOptions(
      await screen.findByLabelText("Model for Answers"),
      "anthropic:claude-opus-5-5",
    );
    await waitFor(() => {
      expect(sent("/settings")).toEqual({
        model_answer: "anthropic:claude-opus-5-5",
      });
    });
  });
});

describe("local model and Offline", () => {
  it("turns Offline on once everything's here", async () => {
    fakeBackend({
      "GET /settings": SETTINGS,
      "GET /models": MODELS,
      "PUT /settings/offline": {},
    });
    open("/settings/local");
    const toggle = await screen.findByRole("switch", { name: "Offline mode" });
    await waitFor(() => {
      expect(toggle).toBeEnabled();
    });
    await userEvent.click(toggle);
    await waitFor(() => {
      expect(sent("/settings/offline", "PUT")).toEqual({ on: true });
    });
  });

  it("holds Offline back until the local model is here", async () => {
    fakeBackend({
      "GET /settings": SETTINGS,
      "GET /models": {
        ...MODELS,
        local: { ...MODELS.local, installed: false },
      },
    });
    open("/settings/local");
    expect(await screen.findByText(/Available once/)).toBeVisible();
    expect(screen.getByRole("switch", { name: "Offline mode" })).toBeDisabled();
  });

  it("says so while Offline is on", async () => {
    fakeBackend({
      "GET /settings": { ...SETTINGS, offline: true },
      "GET /models": MODELS,
    });
    open("/settings/local");
    expect(await screen.findByText(/Offline is on\. /)).toBeVisible();
  });
});

describe("privacy and MCP", () => {
  const SOURCES = [
    {
      id: "s1",
      name: "Home",
      path: "/h",
      sensitivity: "standard",
      mcp_enabled: false,
    },
    {
      id: "s2",
      name: "Taxes",
      path: "/t",
      sensitivity: "private",
      mcp_enabled: false,
    },
  ];

  it("switches MCP for a folder, and never offers it for a Private one", async () => {
    fakeBackend({
      "GET /settings": SETTINGS,
      "GET /sources": SOURCES,
      "PATCH /sources/s1": {},
      "PATCH /settings": {},
    });
    open("/settings/privacy");
    await userEvent.click(
      await screen.findByRole("switch", { name: "MCP access for Home" }),
    );
    await waitFor(() => {
      expect(sent("/sources/s1")).toEqual({ mcp_enabled: true });
    });
    expect(screen.getByText("Private · never")).toBeVisible();
    expect(
      screen.queryByRole("switch", { name: "MCP access for Taxes" }),
    ).toBeNull();
    await userEvent.click(screen.getByRole("radio", { name: /Private/ }));
    await waitFor(() => {
      expect(sent("/settings")).toEqual({ default_sensitivity: "private" });
    });
    await userEvent.click(screen.getByRole("button", { name: "Install" }));
    expect(
      await screen.findByText(/Opened the SecureFolio extension/),
    ).toBeVisible();
  });
});

describe("spending and the cloud log", () => {
  const SPENDING = {
    spent_usd: 4.82,
    cap_usd: 25,
    prices_as_of: "2026-11-01",
    log: [
      {
        id: 1,
        at: 1_700_000_000,
        model: "claude-sonnet-5-5",
        purpose: "answer",
        outcome: "sent",
        document_ids: ["d1"],
        input_tokens: 900,
        output_tokens: 100,
        cost_usd: 0.004,
      },
      {
        id: 2,
        at: 1_700_000_000,
        model: "claude-haiku-5-5",
        purpose: "other",
        outcome: "refused",
        document_ids: [],
        input_tokens: 0,
        output_tokens: 0,
        cost_usd: 0,
      },
    ],
  };

  it("lists each call, and saves a cap only if it's a number", async () => {
    fakeBackend({ "GET /spending": SPENDING, "PATCH /settings": {} });
    open("/settings/spend");
    expect(await screen.findByText("An answer")).toBeVisible();
    expect(screen.getByText("$0.0040")).toBeVisible();
    expect(screen.getByText(/refused, not sent/)).toBeVisible();
    const cap = screen.getByLabelText("Cap in US dollars");
    fireEvent.change(cap, { target: { value: "nope" } });
    expect(screen.getByText(/Enter an amount/)).toBeVisible();
    fireEvent.blur(cap);
    expect(sent("/settings")).toBeUndefined();
    fireEvent.change(cap, { target: { value: "$30" } });
    fireEvent.blur(cap);
    await waitFor(() => {
      expect(sent("/settings")).toEqual({ spend_cap_usd: 30 });
    });
  });
});

describe("backup and import", () => {
  const BACKUPS = {
    folder: "/v/backups",
    last: { at: 1_700_000_000, error: null, size_bytes: 2048 },
    backups: [{ id: "b1", created_at: 1_700_000_000, size_bytes: 2048 }],
  };
  const COMMON = {
    "GET /settings": SETTINGS,
    "GET /import/v1": { at: 1, notes: 1, sessions: 2, saved_queries: 3 },
    "GET /logs/folder": { path: "/v/logs" },
  };

  it("backs up, changes folder, and restores after asking", async () => {
    window.securefolioChosenPaths = ["/elsewhere"];
    fakeBackend({
      ...COMMON,
      "GET /backups": BACKUPS,
      "POST /backups": {},
      "POST /backups/restore": {},
      "PATCH /settings": {},
    });
    open("/settings/backup");
    expect(await screen.findByText(/Last backup:/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Back up now" }));
    expect(posted("/backups")).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Change…" }));
    await waitFor(() => {
      expect(sent("/settings")).toEqual({ backup_folder: "/elsewhere" });
    });
    window.securefolioChosenPaths = undefined;
    await userEvent.click(
      screen.getByRole("button", { name: "Restore from backup…" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(posted("/backups/restore")).toBe(false);
    await userEvent.click(
      screen.getByRole("button", { name: "Restore from backup…" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => {
      expect(sent("/backups/restore", "POST")).toEqual({ id: "b1" });
    });
    await userEvent.click(
      screen.getByRole("switch", { name: "Nightly backup" }),
    );
    await waitFor(() => {
      expect(sent("/settings")).toEqual({ backup_nightly: false });
    });
  });

  it("shows a failed backup, and imports v1 again", async () => {
    window.securefolioChosenPaths = ["/old/metadata.db"];
    fakeBackend({
      ...COMMON,
      "GET /backups": {
        folder: "/v",
        last: { at: 1, error: "The backup failed: full" },
        backups: [],
      },
      "POST /import/v1": refusal("The table notes is missing."),
    });
    open("/settings/backup");
    expect(await screen.findByText(/The backup failed: full/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Restore from backup…" }),
    ).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Import again" }));
    expect(
      await screen.findByText("The table notes is missing."),
    ).toBeVisible();
    window.securefolioChosenPaths = undefined;
    await userEvent.click(
      screen.getByRole("button", { name: "Show in Finder" }),
    );
    await userEvent.click(
      screen.getByRole("switch", { name: "Open at login" }),
    );
  });

  it("says when there's been no backup or import yet", async () => {
    fakeBackend({
      ...COMMON,
      "GET /import/v1": null,
      "GET /backups": { folder: "/v", last: null, backups: [] },
    });
    open("/settings/backup");
    expect(await screen.findByText(/No backup yet/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Choose metadata.db…" }),
    ).toBeVisible();
  });
});
