import { DETAIL, DOC, fakeBackend } from "../fakeBackend";

// The chat that the Ask screen's tests share.

export const THREAD = {
  id: "t1",
  title: "The lease",
  scope: {
    kind: "library",
    include_private: false,
    label: "All · Private excluded",
  },
  mode: "standard",
  private_tainted: false,
  pinned: false,
  memory_off: false,
  updated_at: 1_700_000_000,
  ask_when_indexed: null,
};

export const CITATION = {
  n: 1,
  document_id: "d1",
  revision_id: "r1",
  element_id: "e1",
  title: "Apartment lease",
  label: "Page 6",
  locator: { kind: "pdf", page: 6, rects: [], precision: "page" },
  quote: "Pets are allowed.",
  sensitivity: "standard",
};

export const ANSWER = {
  kind: "answer",
  parts: [
    { text: "One cat is fine. ", cite: [1] },
    { text: "This has no source. ", uncited: true },
  ],
  citations: [CITATION],
  also_searched: [
    {
      document_id: "d2",
      title: "Renters policy",
      element_id: "e2",
      label: "Page 2",
      snippet: "Liability covers pets.",
    },
  ],
  model: "claude-sonnet-5-5",
  local: false,
  mode: "standard",
  cost_usd: 0.02,
  tokens: 900,
  activity: [
    "Searched “pets”",
    "Wrote the answer with Cloud AI, Claude Sonnet 5.5",
  ],
  fell_back: false,
};

export function turn(answer: object) {
  return [
    {
      id: "m1",
      role: "user",
      content: "Can I keep a cat?",
      answer: null,
      created_at: 1_700_000_000,
    },
    {
      id: "m2",
      role: "assistant",
      content: "One cat is fine.",
      answer,
      created_at: 1_700_000_000,
    },
  ];
}

export function chat(
  answer: object = ANSWER,
  extra: object = {},
  thread: object = {},
) {
  fakeBackend({
    "GET /threads": [{ ...THREAD, ...thread }],
    "GET /threads/t1": {
      thread: { ...THREAD, ...thread },
      messages: turn(answer),
      active_run: null,
    },
    "GET /documents/d2": { ...DETAIL, document: { ...DOC, id: "d2" } },
    ...extra,
  });
}
