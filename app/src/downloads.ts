import type { ModelsStatus } from "./api";

// Whether any model is downloading: the search and page models, or Ollama's.

export function isDownloading(models: ModelsStatus | undefined): boolean {
  if (!models) return false;
  const busy = (state: string) =>
    state === "downloading" || state === "verifying";
  return (
    busy(models.search.state) ||
    busy(models.page.state) ||
    models.local.pull.state === "downloading"
  );
}
