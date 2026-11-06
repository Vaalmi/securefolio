import type { DocStatus, DocumentRow, Locator } from "./api";

// How records read on screen.

const DATE = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
});
const KB = 1024;
const WORD = /[\p{L}\p{N}$]+/gu;
const COMMON = new Set(["the", "does", "when", "what", "say", "about", "and"]);

export function fmtDate(iso: string | null): string {
  if (!iso) return "Undated";
  const parsed = new Date(`${iso.slice(0, 10)}T12:00:00`);
  return Number.isNaN(parsed.getTime()) ? iso : DATE.format(parsed);
}

export function fmtMoment(seconds: number | null): string {
  if (seconds === null) return "Not yet";
  return DATE.format(new Date(seconds * 1000));
}

export function fmtSize(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < KB) return `${String(bytes)} B`;
  if (bytes < KB * KB) return `${(bytes / KB).toFixed(0)} KB`;
  return `${(bytes / (KB * KB)).toFixed(1)} MB`;
}

export function docMeta(row: DocumentRow): string {
  const parts = [row.doc_type ?? "", row.file_kind, fmtDate(row.doc_date)];
  if (row.folder) parts.push(row.folder);
  return parts.filter(Boolean).join(" · ");
}

export function needsAttention(status: DocStatus): boolean {
  return ["failed", "skipped_password", "not_downloaded"].includes(status);
}

function tokens(query: string): string[] {
  const words = query.toLowerCase().match(WORD) ?? [];
  return words.filter((word) => word.length > 2 && !COMMON.has(word));
}

/** Splits text so each query word can be wrapped in <mark>. */
export function highlightParts(
  text: string,
  query: string,
): { t: string; m: boolean }[] {
  const words = tokens(query);
  if (words.length === 0) return [{ t: text, m: false }];
  const escaped = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const splitter = new RegExp(`(${escaped.join("|")})`, "gi");
  return text
    .split(splitter)
    .filter(Boolean)
    .map((t) => ({ t, m: words.includes(t.toLowerCase()) }));
}

export function pageOf(locator: Locator | undefined): number | null {
  if (locator?.kind === "pdf") return locator.page;
  if (locator?.kind === "slide") return locator.slide;
  return null;
}

const MODEL_NAMES: Record<string, string> = {
  "claude-sonnet-5-5": "Claude Sonnet 5.5",
  "claude-opus-5-5": "Claude Opus 5.5",
  "claude-haiku-5-5": "Claude Haiku 5.5",
};

// A cloud model's id may carry its provider (`anthropic:claude-sonnet-5-5`); a local
// tag such as `qwen3.5:27b` has a colon of its own, so only a provider is taken off.
const PROVIDER = /^(anthropic|openai|openrouter):/;

export function modelName(id: string): string {
  const bare = id.replace(PROVIDER, "");
  return MODEL_NAMES[bare] ?? bare;
}
