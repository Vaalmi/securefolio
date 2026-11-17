// The types for "This was wrong" reports, as the backend takes them. api.ts
// re-exports them with the rest.

// Where in a document the right answer is, in the form its citations take.
export type ReportPlace =
  | { kind: "page"; page: number }
  | { kind: "slide"; slide: number }
  | { kind: "message"; message: number }
  | { kind: "lines"; start: number; end: number }
  | { kind: "cells"; sheet: string; cells: string };

export interface ReportBody {
  document_id: string | null; // null: it isn't in the library
  place: ReportPlace | null;
  fact: string;
  note: string;
}
