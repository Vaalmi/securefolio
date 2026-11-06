import type { RefObject } from "react";

import { ApiError } from "../../api";
import type { ProviderKey, TaskModelList } from "../../api";

// The Keys card's states, wording and small helpers.

export type KeyState =
  | "idle"
  | "adding"
  | "testing"
  | "ok"
  | "bad"
  | "needs-scope"
  | "saved-needs-scope";

export const ADDING: KeyState[] = ["adding", "testing", "bad", "needs-scope"];

// What a key looks like, and what each provider's ID is for.
export const LOOK: Record<
  ProviderKey["provider"],
  { prefix: string; placeholder?: string; help?: string }
> = {
  anthropic: {
    prefix: "sk-ant-",
    placeholder: "wrkspc_…",
    help: "Only needed if your key isn't tied to one workspace. Find it in the Anthropic Console, under Workspaces.",
  },
  openai: {
    prefix: "sk-proj-",
    placeholder: "proj_…",
    help: "Only needed if your key isn't tied to one project. Find it in OpenAI's settings, under Projects.",
  },
  openrouter: { prefix: "sk-or-" },
};

export const OPENROUTER_NOTE =
  "Your OpenRouter account's settings decide which companies host your requests; SecureFolio sends no preference. Private documents never reach any cloud model. OpenRouter has no batch price, so cloud cards cost full price there.";

export function focusLater(target: RefObject<HTMLButtonElement | null>): void {
  window.setTimeout(() => target.current?.focus(), 0);
}

// A key that needs its ID is refused with 422; any other refusal says why.
export function failureOf(failure: unknown): {
  needsId: boolean;
  message: string;
} {
  if (failure instanceof ApiError)
    return { needsId: failure.status === 422, message: failure.message };
  return { needsId: false, message: String(failure) };
}

// Each task's name and what it covers, as Settings and first run show them.
export const TASKS: Record<
  TaskModelList["task"],
  { title: string; desc: string }
> = {
  answer: { title: "Answers", desc: "Standard mode" },
  deep: { title: "Deep answers", desc: "Asks first when a run is over $0.50" },
  helper: {
    title: "Helpers",
    desc: "Rewriting the question, summaries, memories, and cards for folders where you turned cloud organizing on",
  },
  page: {
    title: "Reading a page you ask about",
    desc: "Reads one page as a picture",
  },
};
