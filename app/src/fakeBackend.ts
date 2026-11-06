import { vi } from "vitest";

// A backend for the screens' tests: each route's answer, by its method and path.

export const BASE = "http://127.0.0.1:1";

export type Routes = Record<string, unknown>;

export const DOC = {
  id: "d1",
  title: "Apartment lease",
  file_name: "Apartment lease.pdf",
  file_kind: "PDF",
  doc_type: "Contract",
  doc_date: "2026-01-02",
  added_at: 1_700_000_000,
  size_bytes: 2_000_000,
  pages: 3,
  status: "indexed",
  status_reason: null,
  sensitivity: "standard",
  starred: false,
  active_revision_id: "r1",
  folder: "Home",
};

export const CARD = {
  state: "complete",
  title: "Apartment lease",
  summary: "A lease for unit 3B.",
  doc_type: "Contract",
  dates: [{ label: "Starts", value: "Aug 1" }],
  people: ["Maria Okafor"],
  organizations: ["Lease Co"],
  amounts: ["$2,350.00"],
  tags: ["home"],
  edited_fields: ["summary"],
  written_by: "Local model",
};

export const DETAIL = {
  document: DOC,
  card: CARD,
  locations: [
    {
      id: "l1",
      path: "/h/Apartment lease.pdf",
      source_id: "s1",
      storage_mode: "managed",
      missing: false,
      dataless: false,
    },
  ],
  collections: [{ id: "c1", name: "House" }],
  view_state: "ready",
  cloud_page_allowed: true,
  has_live_work: false,
};

export const COUNTS = {
  done: 3,
  total: 9,
  active: 6,
  completed: 3,
  failed: 1,
  skipped: 2,
  latest: 1,
};

export const COLLECTION = {
  id: "c1",
  name: "House",
  count: 2,
  titles: ["Apartment lease", "Roof report"],
  has_private: true,
  updated_at: 1_700_000_000,
};

export const ORGANIZE_ESTIMATE = {
  backfill_usd: 1.25,
  per_document_usd: 0.002,
  batched: false,
  model: "anthropic:claude-haiku-5-5",
  model_name: "Claude Haiku 5.5",
  provider: "Anthropic",
  input_usd: 0.1,
  output_usd: 0.5,
  backfill_input_usd: 0.1,
  backfill_output_usd: 0.5,
  key_ready: true,
  unavailable: null,
};

export const PAGE_COST = {
  model: "openai:gpt-6.1-sol",
  model_name: "GPT-6.1 Sol",
  provider: "OpenAI",
  input_usd: 2,
  output_usd: 10,
  cost_usd: 0.085,
  unavailable: null,
};

export const CLOUD_ROUTE = {
  local: false,
  model: "anthropic:claude-sonnet-5-5",
  name: "Claude Sonnet 5.5",
  provider: "anthropic",
  reason: null,
  blocked: null,
  helper_local: false,
  helper_name: "Claude Haiku 5.5",
  deep_cloud: true,
  needs_key: false,
};

export const LOCAL_ROUTE = {
  ...CLOUD_ROUTE,
  local: true,
  model: "gemma4:26b",
  name: "gemma4:26b",
  provider: null,
  helper_local: true,
  helper_name: "gemma4:26b",
  deep_cloud: false,
};

const DEFAULTS: Routes = {
  "GET /health": { version: "2", database: "ready", worker: "running" },
  "GET /library": { rows: [DOC], total: 1, offset: 0 },
  "GET /library/ids": ["d1"],
  "GET /library/facets": {
    types: [{ value: "Contract", count: 1 }],
    folders: [{ value: "s1", label: "Home", count: 1 }],
    tags: [{ value: "home", count: 1 }],
    collections: [{ value: "c1", label: "House" }],
  },
  "GET /library/counts": { total: 1, types: { Contract: 1 } },
  "GET /collections": [COLLECTION],
  "GET /jobs/counts": COUNTS,
  "GET /jobs": [],
  "GET /ask/route": CLOUD_ROUTE,
  "GET /notes": [],
  "GET /threads": [],
  "GET /settings": { setup_done: true, default_sensitivity: "standard" },
  "GET /settings/keys": [],
  "GET /models/tasks": { providers: [], tasks: [] },
  "GET /saved-queries": [],
  "GET /sources": [],
  "GET /search": {
    documents: [],
    passages: [],
    hidden_private: 0,
    semantic: true,
  },
  "GET /documents/d1": DETAIL,
  "GET /documents/d1/file": new ArrayBuffer(4),
  "GET /documents/d1/sheets": [],
  "POST /search/warm": {},
  "POST /documents/d1/opened": {},
};

export const calls: { method: string; path: string; body: unknown }[] = [];

function respond(answer: unknown, status = 200): Response {
  return new Response(JSON.stringify(answer), { status });
}

export function fakeBackend(routes: Routes = {}): void {
  calls.length = 0;
  const table = { ...DEFAULTS, ...routes };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (url === "/__connection") {
        return Promise.resolve(respond({ base: BASE, token: "t" }));
      }
      const full = url.replace(`${BASE}/api`, "");
      const path = full.split("?")[0] ?? full;
      const method = init?.method ?? "GET";
      const body =
        typeof init?.body === "string"
          ? (JSON.parse(init.body) as unknown)
          : undefined;
      calls.push({ method, path: full, body });
      if (path === "/jobs/events") {
        return Promise.resolve(new Response(""));
      }
      const answer = table[`${method} ${path}`];
      if (answer === undefined) return Promise.resolve(respond({}));
      if (typeof answer === "function") {
        const made = (answer as (value: unknown) => unknown)(body);
        if (made instanceof Response) return Promise.resolve(made);
        if (made instanceof Promise) return made;
        return Promise.resolve(respond(made));
      }
      if (answer instanceof ArrayBuffer)
        return Promise.resolve(new Response(answer));
      return Promise.resolve(respond(answer));
    }),
  );
}

export function refusal(detail: string, status = 409): () => Response {
  return () => respond({ detail }, status);
}
