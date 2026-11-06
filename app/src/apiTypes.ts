import type { InstalledModel, LocalJob, ModelMemory } from "./apiModelTypes";

// The records the backend sends.

export type Sensitivity = "standard" | "private";
export type Mode = "standard" | "deep" | "private";
export type DocStatus =
  | "queued"
  | "indexing"
  | "indexed"
  | "failed"
  | "cancelled"
  | "missing"
  | "removed"
  | "not_downloaded"
  | "skipped_password"
  | "skipped_unsupported";

export interface Health {
  version: string;
  database: string;
  worker: string;
}

export interface DocumentRow {
  id: string;
  title: string;
  file_name: string;
  file_kind: string;
  doc_type: string | null;
  doc_date: string | null;
  added_at: number;
  size_bytes: number | null;
  pages: number | null;
  status: DocStatus;
  status_reason: string | null;
  sensitivity: Sensitivity;
  starred: boolean;
  active_revision_id: string | null;
  folder?: string | null;
}

export interface Rect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}
export type Locator =
  | { kind: "pdf"; page: number; rects: Rect[]; precision: string }
  | { kind: "sheet"; sheet: string; range: string }
  | { kind: "slide"; slide: number; rects: Rect[] }
  | { kind: "message"; message: number; start_line: number; end_line: number }
  | { kind: "anchor"; anchor: string }
  | { kind: "lines"; start: number; end: number }
  | { kind: "card"; field: string };

export interface Passage {
  id: string;
  element_id: string | null;
  snippet: string;
  locator: Locator | null;
  label: string;
  document: DocumentRow;
}

export interface SearchResults {
  documents: DocumentRow[];
  passages: Passage[];
  hidden_private: number;
  semantic: boolean;
}

export interface SearchScope {
  q: string;
  include_private: boolean;
  collection?: string;
}

// Who answers a question, as the backend decides it before anything is sent.
export interface AskRoute {
  local: boolean;
  model: string; // provider:model, or the local tag
  name: string; // the cloud model's name, or the local tag
  provider: string | null;
  reason: string | null; // why it answers locally, when it falls back
  blocked: string | null; // why nothing can answer it now
  helper_local: boolean;
  helper_name: string; // the helper's model name, or the local tag
  deep_cloud: boolean; // whether a Deep run would go to the cloud
  needs_key: boolean; // local only for want of the chosen model's provider key
}

export interface LibraryParams {
  view?: string;
  doc_type?: string;
  source?: string;
  collection?: string;
  tag?: string;
  date_from?: string;
  date_to?: string;
  name?: string;
  include_private?: boolean;
  sort?: string;
  descending?: boolean;
  offset?: number;
  limit?: number;
}

export interface LibraryWindow {
  rows: DocumentRow[];
  total: number;
  offset: number;
}

export interface FacetChoice {
  value: string;
  label?: string;
  count?: number;
}

export interface Facets {
  types: FacetChoice[];
  folders: FacetChoice[];
  tags: FacetChoice[];
  collections: FacetChoice[];
}

export interface LibraryCounts {
  total: number;
  types: Record<string, number>;
}

export interface Card {
  state: string;
  title: string;
  summary: string;
  doc_type: string;
  dates: { label: string; value: string }[];
  people: string[];
  organizations: string[];
  amounts: string[];
  tags: string[];
  edited_fields: string[];
  written_by: string;
  state_reason?: string | null; // why a pending card is still waiting for a model
}

export interface DocumentLocation {
  id: string;
  path: string;
  source_id: string;
  storage_mode: string;
  missing: boolean;
  dataless: boolean;
}

export type ViewState =
  "ready" | "missing" | "changed" | "not_downloaded" | "stored";

export interface DocumentDetail {
  document: DocumentRow;
  card: Card | null;
  locations: DocumentLocation[];
  collections: { id: string; name: string }[];
  view_state: ViewState;
  cloud_page_allowed: boolean; // Standard and not Offline: the action is offered
  has_live_work: boolean;
}

export interface StoredElement {
  id: string;
  ordinal: number;
  kind: string;
  text: string;
  locator: Locator;
  label: string;
  reading_state: string | null;
  reading_reason: string | null;
}

export interface StoredText {
  elements: StoredElement[];
  total: number;
}

export interface SheetStart {
  sheet: string;
  first_ordinal: number;
}

export interface CitedElement {
  element: StoredElement | null;
  current: boolean;
}

export interface Source {
  id: string;
  name: string;
  path: string;
  builtin: boolean;
  sensitivity: Sensitivity;
  auto_organize: boolean;
  mcp_enabled: boolean;
  status: string;
  status_reason: string | null;
  last_scan_at: number | null;
  items: number;
}

export type TrayStatus =
  "Queued" | "Indexing" | "Completed" | "Failed" | "Skipped";

export interface TrayRow {
  id: string;
  job_id: string | null;
  document_id: string;
  file: string;
  folder: string;
  status: TrayStatus;
  detail: string;
  skip_kind: string | null;
}

export interface TrayCounts {
  done: number;
  total: number;
  active: number;
  completed: number;
  failed: number;
  skipped: number;
  latest: number;
}

export interface Collection {
  id: string;
  name: string;
  count: number;
  titles: string[];
  has_private: boolean;
  updated_at: number;
}

export interface NoteCitation {
  document_id: string;
  revision_id: string;
  element_id: string | null;
  opens: { state: string; element: StoredElement | null };
}

export interface CitationPlace {
  state: "current" | "moved" | "stored" | "gone";
  element: StoredElement | null;
}

export interface Note {
  id: string;
  title: string;
  body: string;
  citations: NoteCitation[];
  document_id: string | null;
  source_label: string;
  sensitivity: Sensitivity;
  created_at: number;
}

export interface SavedQuery {
  id: string;
  text: string;
}

// Reading one page in the cloud: the page model, its price and the most it costs, or
// why it's unavailable now.
export interface PageCost {
  model: string;
  model_name: string;
  provider: string; // its provider's name
  input_usd: number | null; // per million tokens; null: no known price
  output_usd: number | null;
  cost_usd: number | null;
  unavailable: string | null; // no ready key for its provider, or no known price
}

export type ThreadScope =
  | { kind: "library"; include_private: boolean; label: string }
  | { kind: "collection"; id: string; label: string }
  | { kind: "documents" | "search"; ids: string[]; label: string };

export interface Thread {
  id: string;
  title: string;
  scope: ThreadScope;
  mode: Mode;
  private_tainted: boolean;
  pinned: boolean;
  memory_off: boolean;
  updated_at: number;
  ask_when_indexed: string | null;
}

export interface AnswerCitation {
  n: number;
  document_id: string;
  revision_id: string;
  element_id: string;
  title: string;
  label: string;
  locator: Locator;
  quote: string;
  sensitivity: Sensitivity;
}

export interface AnswerPart {
  text: string;
  cite?: number[];
  uncited?: boolean;
}

export interface SearchedPassage {
  document_id: string;
  title: string;
  element_id: string | null;
  label: string;
  snippet?: string;
  text?: string;
}

export interface TableRow {
  n: number;
  document_id: string;
  title: string;
  doc_date: string | null;
  amount: number | null;
  amount_text: string;
  summary: string;
}

export interface PendingRow {
  document_id: string;
  title: string;
  doc_date: string | null;
  reason: string;
}

export interface Answer {
  counted?: boolean;
  kind: "answer" | "no_evidence" | "list";
  parts?: AnswerPart[];
  citations?: AnswerCitation[];
  also_searched?: SearchedPassage[];
  left_out?: string[];
  coverage?: { searched: number; indexing: number; private_folders: number };
  closest?: SearchedPassage[];
  table?: {
    rows: TableRow[];
    total: number;
    wants_total: boolean;
    pending: PendingRow[];
    unchecked_pending: number;
    shown_to_model: number;
  };
  model: string;
  model_name?: string; // as the answer shows it; older answers have none
  local: boolean;
  helper_local?: boolean; // older answers have none: their helpers ran with them
  mode: Mode;
  cost_usd: number;
  tokens: number;
  activity: string[];
  fell_back: boolean;
}

export interface ThreadMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  answer: Answer | null;
  created_at: number;
}

export type RunCard =
  | {
      kind: "private_in_scope";
      id: string;
      documents: { id: string; title: string }[];
    }
  | {
      kind: "deep_cost";
      id: string;
      question: string;
      model: string;
      model_name: string;
      estimate_usd: number;
      documents: number;
      spent_usd: number;
      cap_usd: number;
    };

export interface Run {
  id: string;
  thread_id: string;
  state: string;
  card: RunCard | null;
  error: string | null;
}

export interface ThreadDetail {
  thread: Thread;
  messages: ThreadMessage[];
  active_run: Run | null;
  last_failure: { error: string | null; question: string | null } | null;
}

export interface Memory {
  id: string;
  kind: string;
  text: string;
  sensitivity: Sensitivity;
  source_label: string;
  pinned: boolean;
  expires_at: number | null;
  created_at: number;
}

export interface OrganizeEstimate {
  backfill_usd: number | null; // null: the helper model has no known price
  per_document_usd: number | null;
  batched: boolean; // at the batch price: a backlog, on a provider with a batch API
  model: string;
  model_name: string;
  provider: string | null; // null for the local model
  input_usd: number | null; // the helper's full price per million tokens
  output_usd: number | null;
  backfill_input_usd: number | null; // the backfill's: the batch price when batched
  backfill_output_usd: number | null;
  key_ready: boolean; // whether cloud organizing can be turned on now
  unavailable: string | null; // why the helper can't write the cards now
}

export interface RelatedDocument {
  document_id: string;
  title: string;
  file_kind: string;
  doc_type: string | null;
  doc_date: string | null;
}

// Settings' records: the settings, models, spending and backup.

export interface AppSettings {
  model_answer: string;
  model_deep: string;
  model_helper: string;
  model_page: string;
  local_answers: string; // the local model for each job, by its Ollama tag
  local_cards: string;
  local_figures: string;
  default_sensitivity: "standard" | "private";
  spend_cap_usd: number;
  setup_done: boolean;
  backup_nightly: boolean;
  backup_folder: string | null;
  offline: boolean;
}

export type SettingsChange = Partial<Omit<AppSettings, "offline">>;

export interface ModelGroup {
  state: "not_installed" | "downloading" | "verifying" | "installed" | "failed";
  bytes_done: number;
  bytes_total: number;
  reason: string | null;
}

export interface ModelsStatus {
  search: ModelGroup;
  page: ModelGroup;
  jobs: Record<LocalJob, { tag: string; problem: string | null }>;
  installed: { local: InstalledModel[]; remote: { name: string }[] };
  memory: ModelMemory; // the chosen models' together
  mac_memory_gb: number;
  local: {
    tag: string;
    recommended: string;
    installed: boolean;
    pull: {
      tag?: string | null; // the one download from Ollama, by tag
      state: string;
      done: number;
      total: number;
      reason: string | null;
    };
  };
  ollama: {
    running: boolean;
    tags: string[];
    refused: string | null;
    no_cloud: boolean;
  };
}

export interface CloudCall {
  id: number;
  at: number;
  model: string;
  provider: string;
  provider_name: string;
  model_name: string;
  purpose: string;
  outcome: string;
  document_ids: string[];
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface Spending {
  spent_usd: number;
  cap_usd: number;
  prices_as_of: string;
  log: CloudCall[];
}

export interface BackupRow {
  id: string;
  created_at: number;
  size_bytes: number;
}

export interface BackupLast {
  at: number;
  error: string | null;
  size_bytes?: number;
}

export interface Backups {
  folder: string;
  last: BackupLast | null;
  backups: BackupRow[];
}

// Settings, Keys: one provider's key, never the key itself.
