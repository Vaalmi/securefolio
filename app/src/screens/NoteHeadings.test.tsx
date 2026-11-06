import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls, DETAIL, DOC, fakeBackend } from "../fakeBackend";
import { installBrowser, open } from "../testApp";

// Note headings, on a note's card and in the notes panel.

beforeEach(() => {
  installBrowser();
});

const NOTE = {
  id: "n1",
  title: "Renew",
  body: "Renew by June.",
  citations: [],
  document_id: "d1",
  source_label: "Written by you",
  sensitivity: "standard",
  created_at: 1_700_000_000,
};

function sent(path: string, method: string) {
  return calls.filter((c) => c.path === path && c.method === method).at(-1)
    ?.body;
}

function notes(list: object[], extra: Record<string, unknown> = {}) {
  fakeBackend({
    "GET /documents/d1": DETAIL,
    "GET /notes": list,
    "POST /notes": NOTE,
    "PATCH /notes/n1": {},
    "DELETE /notes/n1": {},
    ...extra,
  });
}

async function notesTab(): Promise<void> {
  open("/doc/d1");
  await userEvent.click(await screen.findByRole("tab", { name: /Notes/ }));
}

describe("a new note", () => {
  it("takes an optional heading, sent with the text", async () => {
    notes([]);
    await notesTab();
    const heading = screen.getByLabelText("Heading (optional)");
    expect(heading).toHaveAttribute(
      "placeholder",
      "From the first words of the note",
    );
    await userEvent.type(screen.getByLabelText("Text"), "The roof needs work.");
    expect(heading).toHaveAttribute("placeholder", "The roof needs work.");
    await userEvent.type(heading, "Roof");
    await userEvent.click(screen.getByRole("button", { name: "Save note" }));
    await waitFor(() => {
      expect(sent("/notes", "POST")).toEqual({
        body: "The roof needs work.",
        document_id: "d1",
        heading: "Roof",
      });
    });
    expect(
      screen.getByText(
        "Left empty, the heading is the first 40 characters of the text.",
      ),
    ).toBeVisible();
  });

  it("says a Private document's notes are Private", async () => {
    notes([], {
      "GET /documents/d1": {
        ...DETAIL,
        document: { ...DOC, sensitivity: "private" },
      },
    });
    await notesTab();
    expect(
      screen.getByText(
        "Notes on this document are Private, like the document.",
      ),
    ).toBeVisible();
  });
});

describe("editing a note", () => {
  it("edits the heading above the text, saved together", async () => {
    notes([NOTE]);
    await notesTab();
    await userEvent.click(
      await screen.findByRole("button", { name: "Edit note Renew" }),
    );
    const form = screen.getByRole("form", { name: "Edit note Renew" });
    const fields = within(form).getAllByRole("textbox");
    expect(fields.map((f) => f.id)).toEqual(["nt-n1", "nb-n1"]);
    expect(fields[0]).toHaveFocus();
    await userEvent.clear(fields[0]);
    await userEvent.type(fields[0], "Renew the lease");
    await userEvent.type(fields[1], " Call first.");
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(sent("/notes/n1", "PATCH")).toEqual({
        heading: "Renew the lease",
        body: "Renew by June. Call first.",
      });
    });
  });

  it("says what's missing: an empty heading, or empty text", async () => {
    notes([NOTE]);
    await notesTab();
    await userEvent.click(
      await screen.findByRole("button", { name: "Edit note Renew" }),
    );
    const form = screen.getByRole("form", { name: "Edit note Renew" });
    await userEvent.clear(within(form).getByLabelText("Heading"));
    await userEvent.clear(within(form).getByLabelText("Text"));
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));
    expect(form).toHaveTextContent("A note needs a heading.");
    expect(form).toHaveTextContent("A note needs some text.");
    expect(within(form).getByLabelText("Heading")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(sent("/notes/n1", "PATCH")).toBeUndefined();
  });

  it("keeps a Private note Private, and says so", async () => {
    notes([{ ...NOTE, sensitivity: "private" }]);
    await notesTab();
    await userEvent.click(
      await screen.findByRole("button", { name: "Edit note Renew" }),
    );
    expect(
      screen.getByText(
        "This note stays Private. Editing it doesn't change that.",
      ),
    ).toBeVisible();
  });
});

describe("the heading shows", () => {
  it("on the Notes screen, and an edit shows there", async () => {
    let list = [NOTE];
    fakeBackend({
      "GET /notes": () => list,
      "PATCH /notes/n1": (body: { heading: string; body: string }) => {
        list = [{ ...NOTE, title: body.heading, body: body.body }];
        return {};
      },
    });
    open("/notes");
    expect(await screen.findByText("Renew")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Edit note Renew" }),
    );
    const heading = screen.getByLabelText("Heading");
    await userEvent.clear(heading);
    await userEvent.type(heading, "Lease renewal");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Lease renewal")).toBeVisible();
  });

  it("in the deletion notice", async () => {
    notes([NOTE]);
    await notesTab();
    await userEvent.click(
      await screen.findByRole("button", { name: "Delete note Renew" }),
    );
    expect(await screen.findByText("Deleted note “Renew”.")).toBeVisible();
  });
});
