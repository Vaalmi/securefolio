import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { installBrowser, open } from "../testApp";
import { ANSWER, chat } from "./askFixtures";

// When no AI can answer, Ask shows what search found: labelled as not an answer,
// with why, each passage opening its document, and nothing to copy or save.

beforeEach(() => {
  installBrowser();
});

const REASON = "Ollama isn't running on this Mac, so Local AI can't answer.";

function passages(found: object[], followUp = false): object {
  return {
    ...ANSWER,
    kind: "passages",
    parts: undefined,
    citations: [],
    reason: REASON,
    follow_up: followUp,
    coverage: { searched: 12, indexing: 0, private_folders: 0 },
    found,
    model: "",
    local: true,
  };
}

const LEASE = {
  document_id: "d2",
  title: "Renters policy",
  element_id: "e2",
  label: "Page 2",
  text: "Liability covers pets.",
};

describe("passages found when no AI can answer", () => {
  it("say they aren't an answer, why, and open each passage", async () => {
    chat(passages([LEASE]));
    open("/ask/t1");
    const found = await screen.findByRole("article", {
      name: "Passages found, not an answer",
    });
    expect(
      within(found).getByRole("heading", {
        name: "Passages found, no AI answer",
      }),
    ).toBeVisible();
    expect(found).toHaveTextContent(
      "SecureFolio hasn't checked that they answer your question.",
    );
    expect(found).toHaveTextContent(`No AI could answer: ${REASON}`);
    expect(found).toHaveTextContent("Searched 12 documents in");
    expect(within(found).queryByRole("button", { name: "Copy" })).toBeNull();
    expect(
      within(found).queryByRole("button", { name: "Save as note" }),
    ).toBeNull();
    expect(
      within(found).queryByRole("button", { name: "This was wrong" }),
    ).toBeNull();
    await userEvent.click(
      within(found).getByRole("button", {
        name: "Preview Renters policy, Page 2",
      }),
    );
    expect(
      await screen.findByRole("heading", { name: "Preview" }),
    ).toBeVisible();
  });

  it("say a follow-up's were found with your words alone", async () => {
    chat(passages([LEASE], true));
    open("/ask/t1");
    const found = await screen.findByRole("article", {
      name: "Passages found, not an answer",
    });
    expect(found).toHaveTextContent("Found with your words alone.");
  });

  it("say when search found nothing, and how much it searched", async () => {
    chat(passages([]));
    open("/ask/t1");
    const found = await screen.findByRole("article", {
      name: "Passages found, not an answer",
    });
    expect(found).toHaveTextContent(
      "I couldn't find enough evidence in this scope.",
    );
    expect(found).toHaveTextContent("Searched 12 documents in");
    expect(found).toHaveTextContent(`No AI answered: ${REASON}`);
  });
});
