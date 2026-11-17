import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls } from "../fakeBackend";
import { installBrowser, open } from "../testApp";
import { ANSWER, chat } from "./askFixtures";

// A citation opened from a sentence asks where that sentence's figures are, by
// ids alone, and highlights them inside its passage; the viewer gets the same ids.

beforeEach(() => {
  installBrowser();
});

const PASSAGE = "Pets are allowed for a fee of $250.";
const AT = Array.from(PASSAGE).join("").indexOf("$250");

function opened(places: object[]) {
  chat(
    {
      ...ANSWER,
      parts: [{ text: "A pet costs $250. ", cite: [1] }],
    },
    {
      "GET /documents/d1/citation": {
        state: "current",
        element: {
          id: "e1",
          text: PASSAGE,
          locator: { kind: "lines", start: 1, end: 1 },
          label: "Lines 1",
        },
      },
      "GET /documents/d1/elements/e1/figures": { figures: places },
    },
  );
}

async function openCitation(): Promise<void> {
  open("/ask/t1");
  const answer = await screen.findByRole("article", { name: "Answer" });
  await userEvent.click(
    within(answer).getByRole("button", { name: /^Citation 1/ }),
  );
  await screen.findByRole("heading", { name: "Preview" });
}

describe("a citation opened from its sentence", () => {
  it("asks for its figures by ids alone and highlights them", async () => {
    opened([{ kind: "text", text: "$250", range: [AT, AT + 4] }]);
    await openCitation();
    const mark = await screen.findByText("$250", { selector: "mark.hl-fig" });
    expect(mark).toBeVisible();
    expect(screen.getByRole("note")).toHaveTextContent("Highlighted: $250");
    const asked = calls.find((c) => c.path.includes("/figures"));
    expect(asked?.path).toBe(
      "/documents/d1/elements/e1/figures?message=m2&sentence=0",
    );
    expect(asked?.path).not.toContain("$");
  });

  it("opens the viewer with the same ids", async () => {
    opened([{ kind: "text", text: "$250", range: [AT, AT + 4] }]);
    await openCitation();
    await screen.findByText("$250", { selector: "mark.hl-fig" });
    await userEvent.click(
      screen.getByRole("button", { name: "Open in viewer" }),
    );
    await waitFor(() => {
      expect(window.location.hash).toBe("#/doc/d1?el=e1&message=m2&sentence=0");
    });
  });

  it("opens at the passage alone when nothing could be placed", async () => {
    opened([]);
    await openCitation();
    await screen.findByText(PASSAGE);
    expect(document.querySelectorAll("mark.hl-fig")).toHaveLength(0);
    expect(screen.queryByText(/^Highlighted:/)).toBeNull();
  });
});
