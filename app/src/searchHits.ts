import type { DocumentRow, Passage, SearchResults } from "./api";
import { go } from "./router";

export type Hit =
  | { kind: "doc"; id: string; doc: DocumentRow }
  | { kind: "passage"; id: string; doc: DocumentRow; passage: Passage };

export function hitsOf(results: SearchResults | undefined): Hit[] {
  if (!results) return [];
  return [
    ...results.documents.map((doc): Hit => ({
      kind: "doc",
      id: `d-${doc.id}`,
      doc,
    })),
    ...results.passages.map((passage): Hit => ({
      kind: "passage",
      id: `p-${passage.id}`,
      doc: passage.document,
      passage,
    })),
  ];
}

export function openHit(hit: Hit): void {
  if (hit.kind === "doc") go(`/doc/${hit.doc.id}`);
  else go(`/doc/${hit.doc.id}?el=${hit.passage.element_id ?? ""}`);
}
