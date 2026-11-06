import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, fakeBackend, refusal } from "../../fakeBackend";
import { installBrowser, open } from "../../testApp";

// Settings, Local models.

beforeEach(() => {
  installBrowser();
});

const GIB = 1024 ** 3;
const ALL = ["local_answers", "local_cards", "local_figures"];
const GEMMA = {
  name: "gemma4:26b",
  size: 17 * GIB,
  text: true,
  images: true,
  jobs: ALL,
  memory: { gb: 24.7, about: false },
};
const QWEN = {
  name: "qwen3.5:9b",
  size: 6.1 * GIB,
  text: true,
  images: false,
  jobs: ["local_answers", "local_cards"],
  memory: { gb: 6.1, about: true },
};
const EMBED = {
  name: "nomic-embed-text:latest",
  size: 0.3 * GIB,
  text: false,
  images: false,
  jobs: [],
  memory: { gb: 0.3, about: true },
};
const GROUP = { state: "installed", bytes_done: 9, bytes_total: 9 };
const IDLE = {
  tag: null,
  state: "not_installed",
  done: 0,
  total: 0,
  reason: null,
};

interface Job {
  tag: string;
  problem: string | null;
}

function models(changes: Record<string, unknown> = {}) {
  const jobs: Record<string, Job> = {
    local_answers: { tag: "gemma4:26b", problem: null },
    local_cards: { tag: "qwen3.5:9b", problem: null },
    local_figures: { tag: "gemma4:26b", problem: null },
  };
  return {
    search: GROUP,
    page: GROUP,
    jobs,
    installed: {
      local: [GEMMA, QWEN, EMBED],
      remote: [{ name: "gpt-oss:120b-cloud" }],
    },
    memory: { gb: 30.8, about: true },
    mac_memory_gb: 48,
    local: {
      tag: "gemma4:26b",
      recommended: "gemma4:26b",
      installed: true,
      pull: IDLE,
    },
    ollama: {
      running: true,
      tags: ["gemma4:26b"],
      refused: null,
      no_cloud: true,
    },
    ...changes,
  };
}

function backend(found: unknown, extra: Record<string, unknown> = {}) {
  fakeBackend({
    "GET /settings": { setup_done: true, offline: false },
    "GET /models": found,
    "PATCH /settings": {},
    ...extra,
  });
}

function options(label: string): string[] {
  return within(screen.getByLabelText(label))
    .getAllByRole("option")
    .map((o) => o.textContent);
}

describe("Local models", () => {
  it("offers each job only the models that can do it, with the suggested one", async () => {
    backend(models());
    open("/settings/local");
    await screen.findByLabelText("Model for Cards");
    expect(options("Model for Private and Offline answers")).toEqual([
      "gemma4:26b · suggested",
      "qwen3.5:9b",
    ]);
    expect(options("Model for Figures and charts")).toEqual([
      "gemma4:26b · suggested",
    ]);
    expect(screen.getByLabelText("Model for Cards")).toHaveValue("qwen3.5:9b");
  });

  it("saves a job's model", async () => {
    backend(models());
    open("/settings/local");
    await userEvent.selectOptions(
      await screen.findByLabelText("Model for Cards"),
      "gemma4:26b",
    );
    await waitFor(() => {
      const saved = calls.find((c) => c.method === "PATCH");
      expect(saved?.body).toEqual({ local_cards: "gemma4:26b" });
    });
  });

  it("says the memory they need together, and this Mac's", async () => {
    backend(models());
    open("/settings/local");
    const line = await screen.findByText(/Together they need/);
    expect(line).toHaveTextContent(
      "Together they need about 30.8 GB of memory while loaded. This Mac has 48 GB.",
    );
  });

  it("lists what's installed, with Ollama's cloud models never used locally", async () => {
    backend(models());
    open("/settings/local");
    const table = await screen.findByRole("table", {
      name: "Models installed in Ollama",
    });
    const rows = within(table).getAllByRole("row");
    expect(rows.map((r) => r.textContent)).toEqual([
      "ModelSizeMemoryTakes imagesUsed for",
      "gemma4:26b17.0 GB24.7 GBYesAnswers, Figures and charts",
      "qwen3.5:9b6.1 GBabout 6.1 GBNoCards",
      "nomic-embed-text:latest0.3 GBabout 0.3 GBNoNot used",
      "gpt-oss:120b-cloud––– Runs on Ollama's servers, so never used as a local model",
    ]);
    expect(
      screen.getByText(/Suggested for this Mac's 48 GB:/),
    ).toHaveTextContent("Suggested for this Mac's 48 GB: gemma4:26b.");
  });

  it("warns when a chosen model was removed from Ollama", async () => {
    const found = models();
    found.jobs.local_cards = {
      tag: "llama3.2:3b",
      problem: "llama3.2:3b isn't installed in Ollama. Download it first.",
    };
    backend(found);
    open("/settings/local");
    expect(
      await screen.findByText(
        "llama3.2:3b is no longer in Ollama. Choose another model; cards wait as pending until you do.",
      ),
    ).toBeVisible();
    expect(screen.getByLabelText("Model for Cards")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(screen.getByLabelText("Model for Cards")).toHaveValue("");
  });

  it("keeps the jobs' warnings and the list when no model left can do a job", async () => {
    // Only an embedding model and a cloud one are left in Ollama.
    const found = models();
    for (const job of ALL) {
      found.jobs[job] = {
        tag: "gemma4:26b",
        problem: "gemma4:26b isn't installed in Ollama. Download it first.",
      };
    }
    backend({
      ...found,
      installed: { local: [EMBED], remote: [{ name: "gpt-oss:120b-cloud" }] },
      local: { ...found.local, installed: false },
    });
    open("/settings/local");
    expect(
      await screen.findByText(
        "gemma4:26b is no longer in Ollama. Choose another model; cards wait as pending until you do.",
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("table", { name: "Models installed in Ollama" }),
    ).toHaveTextContent("nomic-embed-text:latest");
    expect(
      screen.getByText(/Runs on Ollama's servers, so never used/),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: /Download local model/ }),
    ).toBeVisible();
  });

  it("keeps the jobs' warnings when nothing at all is left in Ollama", async () => {
    // Every installed model removed.
    const found = models();
    found.jobs.local_cards = {
      tag: "gemma4:26b",
      problem: "gemma4:26b isn't installed in Ollama. Download it first.",
    };
    backend({
      ...found,
      installed: { local: [], remote: [] },
      local: { ...found.local, installed: false },
    });
    open("/settings/local");
    expect(
      await screen.findByText(
        "gemma4:26b is no longer in Ollama. Choose another model; cards wait as pending until you do.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Nothing is installed in Ollama.")).toBeVisible();
  });
});

describe("Local models, downloads", () => {
  it("downloads a model by name, with its progress, then says it's installed", async () => {
    let pull: Record<string, unknown> = IDLE;
    backend(null, {
      "GET /models": () => models({ local: { ...models().local, pull } }),
      "POST /models/local/pull": () => {
        pull = {
          tag: "qwen3.5:27b",
          state: "downloading",
          done: 7 * GIB,
          total: 17 * GIB,
          reason: null,
        };
        return {};
      },
    });
    open("/settings/local");
    await userEvent.type(
      await screen.findByLabelText("Ollama model name"),
      "qwen3.5:27b",
    );
    await userEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(
      await screen.findByRole("progressbar", {
        name: "Downloading qwen3.5:27b",
      }),
    ).toHaveAttribute("aria-valuenow", "41");
    expect(screen.getByText(/7\.0 GB of 17\.0 GB/)).toBeVisible();
    expect(screen.getByLabelText("Ollama model name")).toHaveAttribute(
      "readonly",
    );
    pull = { ...pull, state: "installed" };
    await waitFor(
      () => {
        expect(document.getElementById("pull-status")).toHaveTextContent(
          "qwen3.5:27b is installed. Choose it for a job above.",
        );
      },
      { timeout: 3000 },
    );
  });

  it("says Ollama has no such model", async () => {
    backend(
      models({
        local: {
          ...models().local,
          pull: {
            tag: "qwen3.5:72b",
            state: "failed",
            done: 0,
            total: 0,
            reason:
              "Ollama has no model called qwen3.5:72b. Check the name on ollama.com.",
          },
        },
      }),
    );
    open("/settings/local");
    expect(
      await screen.findByText(/Ollama has no model called qwen3\.5:72b/),
    ).toBeVisible();
  });

  it("refuses a cloud name, saying why", async () => {
    backend(models(), {
      "POST /models/local/pull": refusal(
        "gpt-oss:120b-cloud runs on Ollama's servers, not on this Mac.",
        422,
      ),
    });
    open("/settings/local");
    await userEvent.type(
      await screen.findByLabelText("Ollama model name"),
      "gpt-oss:120b-cloud",
    );
    await userEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(
      await screen.findByText(/runs on Ollama's servers, not on this Mac/),
    ).toBeVisible();
  });

  it("asks for no download while Offline is on", async () => {
    fakeBackend({
      "GET /settings": { setup_done: true, offline: true },
      "GET /models": models(),
    });
    open("/settings/local");
    expect(await screen.findByLabelText("Ollama model name")).toBeDisabled();
    expect(
      screen.getByText(
        "Offline is on: SecureFolio asks Ollama for no downloads.",
      ),
    ).toBeVisible();
  });
});

describe("Local models, one download at a time", () => {
  it("sends one download for a double click, and its progress stays in view", async () => {
    // The second click mustn't follow before the status says it's running.
    let pull: Record<string, unknown> = IDLE;
    let answer: (() => void) | undefined;
    backend(null, {
      "GET /models": () => models({ local: { ...models().local, pull } }),
      "POST /models/local/pull": () =>
        new Promise<Response>((resolve) => {
          answer = () => {
            pull = {
              tag: "qwen3.5:27b",
              state: "downloading",
              done: GIB,
              total: 4 * GIB,
              reason: null,
            };
            resolve(new Response("{}"));
          };
        }),
    });
    open("/settings/local");
    await userEvent.type(
      await screen.findByLabelText("Ollama model name"),
      "qwen3.5:27b",
    );
    const download = screen.getByRole("button", { name: "Download" });
    await userEvent.dblClick(download);
    expect(download).toBeDisabled();
    await waitFor(() => {
      expect(answer).toBeDefined();
    });
    answer?.();
    expect(
      await screen.findByRole("progressbar", {
        name: "Downloading qwen3.5:27b",
      }),
    ).toBeVisible();
    const sent = calls.filter((c) => c.path === "/models/local/pull");
    expect(sent).toHaveLength(1);
  });

  it("keeps the form blocked until the status says the download is running", async () => {
    // The request answered, the status not yet reloaded.
    let pull: Record<string, unknown> = IDLE;
    let reload: (() => void) | undefined;
    let loads = 0;
    backend(null, {
      "GET /models": () => {
        loads += 1;
        const now = models({ local: { ...models().local, pull } });
        if (loads === 1) return now;
        return new Promise<Response>((resolve) => {
          reload = () => {
            resolve(new Response(JSON.stringify(now)));
          };
        });
      },
      "POST /models/local/pull": () => {
        pull = {
          tag: "qwen3.5:27b",
          state: "downloading",
          done: GIB,
          total: 4 * GIB,
          reason: null,
        };
        return {};
      },
    });
    open("/settings/local");
    await userEvent.type(
      await screen.findByLabelText("Ollama model name"),
      "qwen3.5:27b",
    );
    const download = screen.getByRole("button", { name: "Download" });
    await userEvent.click(download);
    await waitFor(() => {
      expect(reload).toBeDefined();
    });
    expect(download).toBeDisabled();
    await userEvent.click(download);
    expect(calls.filter((c) => c.path === "/models/local/pull")).toHaveLength(
      1,
    );
    reload?.();
    expect(
      await screen.findByRole("progressbar", {
        name: "Downloading qwen3.5:27b",
      }),
    ).toBeVisible();
  });

  it("shows a finished download beside a refusal of the next name", async () => {
    // A refusal is about the name it refused, and hides nothing.
    backend(
      models({
        local: {
          ...models().local,
          pull: { ...IDLE, tag: "qwen3.5:27b", state: "installed" },
        },
      }),
      {
        "POST /models/local/pull": refusal(
          "gpt-oss:120b-cloud runs on Ollama's servers, not on this Mac.",
          422,
        ),
      },
    );
    open("/settings/local");
    await userEvent.type(
      await screen.findByLabelText("Ollama model name"),
      "gpt-oss:120b-cloud",
    );
    await userEvent.click(screen.getByRole("button", { name: "Download" }));
    const status = document.getElementById("pull-status");
    await waitFor(() => {
      expect(status).toHaveTextContent(/runs on Ollama's servers/);
    });
    expect(status).toHaveTextContent("qwen3.5:27b is installed.");
  });
});
