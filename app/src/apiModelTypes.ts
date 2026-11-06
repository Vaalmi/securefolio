// The types for keys and models, as the backend sends them. api.ts re-exports them
// with the rest.

export type LocalJob = "local_answers" | "local_cards" | "local_figures";

export interface ModelMemory {
  gb: number;
  about: boolean; // true: estimated from a size on disk, until Ollama has loaded it
}

export interface InstalledModel {
  name: string; // its Ollama tag
  size: number; // bytes on disk
  text: boolean | null; // null: Ollama hasn't said yet
  images: boolean | null;
  jobs: LocalJob[]; // the jobs it's offered for, by what Ollama says it can do
  memory: ModelMemory;
}

export interface ProviderKey {
  provider: "anthropic" | "openai" | "openrouter";
  name: string;
  present: boolean;
  hint: string | null; // its last four characters, masked
  id_label: string | null; // "Workspace ID", "Project ID", or none
  scope_id: string | null;
  needs_id: boolean; // its test found it needs its ID, and none is saved yet
}

export interface OpenRouterList {
  state: "none" | "fresh" | "stale";
  fetched_at: number | null;
  failed: string | null;
}

export type CloudProvider = ProviderKey["provider"];

export interface CloudModel {
  id: string; // provider:model
  name: string;
  provider: CloudProvider;
  input_usd: number | null; // per million tokens; null: no known price
  output_usd: number | null;
  measured: boolean; // by the evaluation, as the Answer model
  listed: boolean; // false: OpenRouter's newest list left it out
}

export interface TaskModelList {
  task: "answer" | "deep" | "helper" | "page";
  field: "model_answer" | "model_deep" | "model_helper" | "model_page";
  chosen: string; // a model id, or "local"
  may_run_locally: boolean;
  models: CloudModel[];
}

export interface TaskModels {
  providers: {
    id: CloudProvider;
    name: string;
    key_ready: boolean; // a key, and its ID where its test found it needs one
    id_label: string | null;
    needs_id: boolean; // the key is saved, but not ready until its ID works
  }[];
  tasks: TaskModelList[];
}
