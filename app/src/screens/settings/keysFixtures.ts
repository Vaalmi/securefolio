import { calls, fakeBackend } from "../../fakeBackend";

// What Settings, Keys' tests share: Settings' record, a provider's key row, the
// backend with them, and the last body sent to a path.

export const SETTINGS = {
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

export function key(provider: string, changes: object = {}) {
  const names: Record<string, [string, string | null]> = {
    anthropic: ["Anthropic", "Workspace ID"],
    openai: ["OpenAI", "Project ID"],
    openrouter: ["OpenRouter", null],
  };
  const [name, idLabel] = names[provider] ?? [provider, null];
  return {
    provider,
    name,
    present: false,
    hint: null,
    id_label: idLabel,
    scope_id: null as string | null,
    needs_id: false,
    ...changes,
  };
}

export function backend(keys: object[], extra: Record<string, unknown> = {}) {
  fakeBackend({
    "GET /settings": SETTINGS,
    "GET /settings/keys": keys,
    "GET /models/openrouter": {
      state: "fresh",
      fetched_at: 1793900000,
      failed: null,
    },
    ...extra,
  });
}

export function sent(path: string, method: string) {
  return calls.filter((c) => c.path === path && c.method === method).at(-1)
    ?.body;
}
