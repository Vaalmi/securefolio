import { del, get, getBytes, patch, post, put, stream } from "./connection";
import type { StreamEvent } from "./connection";

export { ApiError } from "./connection";

import type {
  AskRoute,
  Card,
  CitedElement,
  Collection,
  DocumentDetail,
  Facets,
  Health,
  LibraryCounts,
  LibraryParams,
  LibraryWindow,
  Memory,
  Mode,
  Note,
  CitationPlace,
  OrganizeEstimate,
  PageCost,
  RelatedDocument,
  Run,
  SavedQuery,
  SearchResults,
  SearchScope,
  Sensitivity,
  SheetStart,
  Source,
  StoredText,
  Thread,
  ThreadDetail,
  ThreadScope,
  TrayCounts,
  TrayRow,
  AppSettings,
  SettingsChange,
  ModelsStatus,
  Spending,
  BackupLast,
  Backups,
} from "./apiTypes";
import type { OpenRouterList, ProviderKey, TaskModels } from "./apiModelTypes";

export type * from "./apiTypes";
export type * from "./apiModelTypes";

// One function per backend operation the app calls. No other module builds a URL.

type Query = Record<string, string | number | boolean | string[] | undefined>;

function withQuery(path: string, query: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    // A list goes as the key repeated, as the backend reads it.
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, item);
    } else if (value !== undefined) params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `${path}?${text}` : path;
}

export function getHealth(): Promise<Health> {
  return get<Health>("/health");
}

export function restartWorker(): Promise<object> {
  return post("/worker/restart");
}

export function search(scope: SearchScope): Promise<SearchResults> {
  return get<SearchResults>(withQuery("/search", { ...scope }));
}

export function warmSearch(): Promise<object> {
  return post("/search/warm");
}

// Who would answer, for this mode and scope: the library (with or without Private),
// a collection, chosen documents, or a thread.
export interface RouteQuery {
  mode: Mode;
  include_private?: boolean;
  collection?: string;
  documents?: string[];
  thread?: string;
}

export function getAskRoute(query: RouteQuery): Promise<AskRoute> {
  return get<AskRoute>(withQuery("/ask/route", { ...query }));
}

export function listLibrary(params: LibraryParams): Promise<LibraryWindow> {
  return get<LibraryWindow>(withQuery("/library", { ...params }));
}

export function listLibraryIds(params: LibraryParams): Promise<string[]> {
  return get<string[]>(withQuery("/library/ids", { ...params }));
}

export function getLibraryFacets(params: LibraryParams): Promise<Facets> {
  return get<Facets>(withQuery("/library/facets", { ...params }));
}

export function getLibraryCounts(): Promise<LibraryCounts> {
  return get<LibraryCounts>("/library/counts");
}

export function getDocument(id: string): Promise<DocumentDetail> {
  return get<DocumentDetail>(`/documents/${id}`);
}

export function getDocumentFile(id: string): Promise<ArrayBuffer> {
  return getBytes(`/documents/${id}/file`);
}

export function getDocumentPreview(id: string): Promise<ArrayBuffer> {
  return getBytes(`/documents/${id}/preview`);
}

export function getDocumentText(
  id: string,
  offset = 0,
  limit = 2000,
): Promise<StoredText> {
  return get<StoredText>(withQuery(`/documents/${id}/text`, { offset, limit }));
}

export function getDocumentPage(id: string, page: number): Promise<StoredText> {
  return get<StoredText>(`/documents/${id}/pages/${String(page)}`);
}

export function getDocumentSheets(id: string): Promise<SheetStart[]> {
  return get<SheetStart[]>(`/documents/${id}/sheets`);
}

export function getChunkElement(
  id: string,
  chunk: string,
): Promise<{ element_id: string | null }> {
  return get(`/documents/${id}/chunks/${chunk}`);
}

export function getCitedElement(
  id: string,
  element: string,
): Promise<CitedElement> {
  return get<CitedElement>(`/documents/${id}/elements/${element}`);
}

export function markDocumentOpened(id: string): Promise<object> {
  return post(`/documents/${id}/opened`);
}

export function setDocumentStar(id: string, starred: boolean): Promise<object> {
  return put(`/documents/${id}/star`, { starred });
}

export function editDocumentCard(
  id: string,
  fields: Partial<Card>,
): Promise<Card> {
  return patch<Card>(`/documents/${id}/card`, fields);
}

export function removeDocuments(ids: string[]): Promise<object> {
  return post("/documents/remove", { document_ids: ids });
}

export function reindexDocument(id: string): Promise<object> {
  return post(`/documents/${id}/reindex`);
}

export function unlockDocument(id: string, password: string): Promise<object> {
  return post(`/documents/${id}/unlock`, { password });
}

export function downloadDocument(id: string): Promise<object> {
  return post(`/documents/${id}/download`);
}

export function relocateDocument(
  id: string,
  path: string,
  confirmedNewVersion = false,
): Promise<object> {
  return post(`/documents/${id}/relocate`, {
    path,
    confirmed_new_version: confirmedNewVersion,
  });
}

export function trashManagedCopy(id: string): Promise<object> {
  return post(`/documents/${id}/trash-managed-copy`);
}

export function rereadPage(id: string, page: number): Promise<object> {
  return post(`/documents/${id}/reread-page`, { page });
}

export function getCloudPageCost(id: string): Promise<PageCost> {
  return get<PageCost>(`/documents/${id}/cloud-read-page`);
}

// The model and price its cost was shown at go with it: the backend sends the page at
// those, or refuses if Settings has changed since.
// The page goes at the model and price shown, from the revision shown, or the
// backend refuses it.
export function cloudReadPage(
  id: string,
  page: number,
  shown: PageCost,
  revisionId: string,
): Promise<object> {
  return post(`/documents/${id}/cloud-read-page`, {
    page,
    model: shown.model,
    input_usd: shown.input_usd,
    output_usd: shown.output_usd,
    revision_id: revisionId,
  });
}

export function importPaths(
  paths: string[],
  sensitivity?: Sensitivity,
): Promise<object> {
  return post("/imports", { paths, ...(sensitivity ? { sensitivity } : {}) });
}

export function listSources(): Promise<Source[]> {
  return get<Source[]>("/sources");
}

export function addSource(
  path: string,
  sensitivity: Sensitivity,
  autoOrganize: boolean,
  mcpEnabled: boolean,
): Promise<Source> {
  return post<Source>("/sources", {
    path,
    sensitivity,
    auto_organize: autoOrganize,
    mcp_enabled: mcpEnabled,
  });
}

// Turning organizing on carries the helper model and price its estimate showed, or
// the backend refuses it.
export function updateSource(
  id: string,
  changes: Partial<
    Pick<Source, "sensitivity" | "auto_organize" | "mcp_enabled"> &
      Pick<OrganizeEstimate, "model" | "input_usd" | "output_usd" | "batched">
  >,
): Promise<object> {
  return patch(`/sources/${id}`, changes);
}

export function rescanSource(id: string): Promise<object> {
  return post(`/sources/${id}/rescan`);
}

export function pauseSource(id: string): Promise<object> {
  return post(`/sources/${id}/pause`);
}

export function resumeSource(id: string): Promise<object> {
  return post(`/sources/${id}/resume`);
}

export function stopWatchingSource(id: string): Promise<object> {
  return del(`/sources/${id}`);
}

export function listJobs(tab: string, limit = 100): Promise<TrayRow[]> {
  return get<TrayRow[]>(withQuery("/jobs", { tab, limit }));
}

export function getJobCounts(): Promise<TrayCounts> {
  return get<TrayCounts>("/jobs/counts");
}

export function cancelJob(id: string): Promise<object> {
  return post(`/jobs/${id}/cancel`);
}

export function retryJob(id: string): Promise<object> {
  return post(`/jobs/${id}/retry`);
}

export function followJobs(
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  return stream("/jobs/events", onEvent, { signal });
}

export function listCollections(): Promise<Collection[]> {
  return get<Collection[]>("/collections");
}

export function createCollection(name: string): Promise<Collection> {
  return post<Collection>("/collections", { name });
}

export function addToCollection(id: string, ids: string[]): Promise<object> {
  return post(`/collections/${id}/add`, { document_ids: ids });
}

export function removeFromCollection(
  id: string,
  ids: string[],
): Promise<object> {
  return post(`/collections/${id}/remove`, { document_ids: ids });
}

export function listNotes(documentId?: string): Promise<Note[]> {
  return get<Note[]>(withQuery("/notes", { document: documentId }));
}

// No heading, or an empty one: the backend takes the text's first 40 characters.
export function createNote(
  body: string,
  documentId: string | null,
  heading = "",
): Promise<Note> {
  return post<Note>("/notes", { body, document_id: documentId, heading });
}

// The heading and the text change together.
export function editNote(
  id: string,
  heading: string,
  body: string,
): Promise<object> {
  return patch(`/notes/${id}`, { heading, body });
}

export function deleteNote(id: string): Promise<object> {
  return del(`/notes/${id}`);
}

export function listSavedQueries(): Promise<SavedQuery[]> {
  return get<SavedQuery[]>("/saved-queries");
}

export const THREAD_PAGE = 50;

export function listThreads(offset = 0): Promise<Thread[]> {
  return get<Thread[]>(`/threads?offset=${String(offset)}`);
}

export function resolveCitation(
  documentId: string,
  revisionId: string,
  elementId: string,
): Promise<CitationPlace> {
  return get<CitationPlace>(
    `/documents/${documentId}/citation?revision_id=${revisionId}&element_id=${elementId}`,
  );
}

export function createThread(scope: ThreadScope, mode: Mode): Promise<Thread> {
  return post<Thread>("/threads", { scope, mode });
}

export function getThread(id: string): Promise<ThreadDetail> {
  return get<ThreadDetail>(`/threads/${id}`);
}

export function updateThread(
  id: string,
  changes: Partial<Pick<Thread, "title" | "pinned" | "memory_off">>,
): Promise<object> {
  return patch(`/threads/${id}`, changes);
}

export function deleteThread(id: string): Promise<object> {
  return del(`/threads/${id}`);
}

export function setThreadScope(
  id: string,
  scope: ThreadScope,
  consented: boolean,
): Promise<{
  needs_consent: boolean;
  documents: { id: string; title: string }[];
}> {
  return post(`/threads/${id}/scope`, { scope, consented });
}

export function sendMessage(
  id: string,
  text: string,
  mode: Mode,
): Promise<Run> {
  return post<Run>(`/threads/${id}/messages`, { text, mode });
}

export function resumeThread(
  id: string,
  cardId: string,
  choice: "local" | "leave" | "deep" | "standard",
): Promise<Run> {
  return post<Run>(`/threads/${id}/resume`, { card_id: cardId, choice });
}

export function stopThread(id: string): Promise<object> {
  return post(`/threads/${id}/stop`);
}

export function askWhenIndexed(id: string, text: string): Promise<object> {
  return post(`/threads/${id}/ask-when-indexed`, { text });
}

export function followRun(
  id: string,
  after: number,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  return stream(withQuery(`/runs/${id}/events`, { after }), onEvent, {
    signal,
  });
}

export function saveAnswerAsNote(messageId: string): Promise<object> {
  return post(`/messages/${messageId}/note`);
}

export function getRelatedDocuments(id: string): Promise<RelatedDocument[]> {
  return get<RelatedDocument[]>(`/documents/${id}/related`);
}

export function listMemories(): Promise<Memory[]> {
  return get<Memory[]>("/memories");
}

export function editMemory(
  id: string,
  changes: { text?: string; pinned?: boolean },
): Promise<object> {
  return patch(`/memories/${id}`, changes);
}

export function deleteMemory(id: string): Promise<object> {
  return del(`/memories/${id}`);
}

export function getMemorySwitch(): Promise<{ on: boolean }> {
  return get<{ on: boolean }>("/memories/switch");
}

export function setMemorySwitch(on: boolean): Promise<object> {
  return put("/memories/switch", { on });
}

export function getOrganizeEstimate(id: string): Promise<OrganizeEstimate> {
  return get<OrganizeEstimate>(`/sources/${id}/organize-estimate`);
}

// Settings: the key, models, spending and backup.

export function getSettings(): Promise<AppSettings> {
  return get<AppSettings>("/settings");
}

export function updateSettings(changes: SettingsChange): Promise<object> {
  return patch("/settings", changes);
}

export function setOffline(on: boolean): Promise<object> {
  return put("/settings/offline", { on });
}

export function getModels(): Promise<ModelsStatus> {
  return get<ModelsStatus>("/models");
}

export function downloadModels(): Promise<object> {
  return post("/models/download");
}

export function downloadLocalModel(): Promise<object> {
  return post("/models/local/download");
}

export function pullLocalModel(tag: string): Promise<object> {
  return post("/models/local/pull", { tag });
}

export function getSpending(offset = 0): Promise<Spending> {
  return get<Spending>(`/spending?offset=${String(offset)}`);
}

export function listBackups(): Promise<Backups> {
  return get<Backups>("/backups");
}

export function backUpNow(): Promise<BackupLast> {
  return post<BackupLast>("/backups");
}

export function restoreBackup(id: string): Promise<object> {
  return post("/backups/restore", { id });
}

export function getLogFolder(): Promise<{ path: string }> {
  return get("/logs/folder");
}

export function listKeys(): Promise<ProviderKey[]> {
  return get<ProviderKey[]>("/settings/keys");
}

// No key: the saved one. No ID: the saved one, if any.
export function testProviderKey(
  provider: string,
  test: { key?: string; scope_id?: string },
): Promise<ProviderKey> {
  return post<ProviderKey>(`/settings/keys/${provider}/test`, test);
}

export function setKeyScope(
  provider: string,
  scopeId: string,
): Promise<ProviderKey> {
  return put<ProviderKey>(`/settings/keys/${provider}/scope`, {
    scope_id: scopeId,
  });
}

export function removeKey(provider: string): Promise<ProviderKey> {
  return del<ProviderKey>(`/settings/keys/${provider}`);
}

export function getOpenrouterList(): Promise<OpenRouterList> {
  return get<OpenRouterList>("/models/openrouter");
}

export function refreshOpenrouterList(): Promise<OpenRouterList> {
  return post<OpenRouterList>("/models/openrouter/refresh");
}

export function listTaskModels(): Promise<TaskModels> {
  return get<TaskModels>("/models/tasks");
}
