import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { installBrowser, open } from "../testApp";
import { ANSWER, chat } from "./askFixtures";

// A figure the check found isn't in its sentence's passages: marked as an uncited
// sentence is, with a tag whose explanation opens on hover, focus or a click.

beforeEach(() => {
  installBrowser();
});

const TEXT = "Rent is $2,350, or $28,200 a year. ";
const START = TEXT.indexOf("$28,200");

function figured(): void {
  chat({
    ...ANSWER,
    parts: [
      {
        text: TEXT,
        cite: [1],
        figures: [
          {
            text: "$2,350",
            kind: "money",
            start: 8,
            end: 14,
            state: "shown",
            cite: 1,
          },
          {
            text: "$28,200",
            kind: "money",
            start: START,
            end: START + 7,
            state: "unshown",
          },
        ],
      },
    ],
  });
}

describe("a figure not in its sources", () => {
  it("is marked, and its tag says why on focus until Escape", async () => {
    figured();
    open("/ask/t1");
    const answer = await screen.findByRole("article", { name: "Answer" });
    const marked = [...answer.querySelectorAll(".unshown")];
    expect(marked.map((m) => m.textContent)).toEqual(["$28,200"]);
    const tag = within(answer).getByRole("button", {
      name: /Not in its sources/,
    });
    const tip = screen.getByRole("tooltip", { hidden: true });
    expect(tag).toHaveAttribute("aria-describedby", tip.id);
    expect(tip).not.toBeVisible();
    fireEvent.focusIn(tag);
    expect(tip).toBeVisible();
    expect(tip).toHaveTextContent(
      "$28,200 isn't in the passage this sentence cites. It may have been worked out by the model. Open the citation to check.",
    );
    fireEvent.keyDown(tag, { key: "Escape" });
    expect(tip).not.toBeVisible();
  });

  it("opens on a click and on hover too", async () => {
    figured();
    open("/ask/t1");
    const answer = await screen.findByRole("article", { name: "Answer" });
    const tag = within(answer).getByRole("button", {
      name: /Not in its sources/,
    });
    const tip = screen.getByRole("tooltip", { hidden: true });
    await userEvent.click(tag);
    expect(tip).toBeVisible();
    fireEvent.keyDown(tag, { key: "Escape" });
    await userEvent.hover(tag);
    expect(tip).toBeVisible();
  });

  it("tags a figure SecureFolio worked out, and never marks it", async () => {
    // A sentence repeating a list's total.
    const said = "The total is $1,284.00. ";
    chat({
      ...ANSWER,
      parts: [
        {
          text: said,
          cite: [1],
          figures: [
            {
              text: "$1,284.00",
              kind: "money",
              start: said.indexOf("$"),
              end: said.indexOf("$") + 9,
              state: "worked",
            },
          ],
        },
      ],
    });
    open("/ask/t1");
    const answer = await screen.findByRole("article", { name: "Answer" });
    expect(
      within(answer).getByRole("button", { name: /Worked out/ }),
    ).toBeVisible();
    expect(answer.querySelectorAll(".unshown")).toHaveLength(0);
    expect(
      within(answer).queryByRole("button", { name: /Not in its sources/ }),
    ).toBeNull();
  });

  it("isn't shown for an answer with no figures marked", async () => {
    chat();
    open("/ask/t1");
    const answer = await screen.findByRole("article", { name: "Answer" });
    expect(answer.querySelectorAll(".unshown")).toHaveLength(0);
    expect(
      within(answer).queryByRole("button", { name: /Not in its sources/ }),
    ).toBeNull();
  });

  it("marks the right characters after an emoji", async () => {
    // Offsets count characters, as the backend's Python does.
    const text = "\u{1F3E0} Rent is $28,200 a year. ";
    chat({
      ...ANSWER,
      parts: [
        {
          text,
          cite: [1],
          figures: [
            {
              text: "$28,200",
              kind: "money",
              start: 10,
              end: 17,
              state: "unshown",
            },
          ],
        },
      ],
    });
    open("/ask/t1");
    const answer = await screen.findByRole("article", { name: "Answer" });
    const marked = [...answer.querySelectorAll(".unshown")];
    expect(marked.map((m) => m.textContent)).toEqual(["$28,200"]);
  });

  it("opens below its tag when the pane's top would cut it off", async () => {
    // A tag scrolled to just below
    // the pane's top edge, where an explanation above it can't be seen.
    figured();
    open("/ask/t1");
    const answer = await screen.findByRole("article", { name: "Answer" });
    const tag = within(answer).getByRole("button", {
      name: /Not in its sources/,
    });
    const tip = screen.getByRole("tooltip", { hidden: true });
    const pane = answer.closest(".pane");
    const wrap = tag.parentElement;
    if (!(pane instanceof HTMLElement) || !wrap) throw new Error("no pane");
    const at = (top: number) => () =>
      DOMRect.fromRect({ x: 0, y: top, width: 200, height: 20 });
    pane.getBoundingClientRect = () =>
      DOMRect.fromRect({ x: 0, y: 100, width: 900, height: 600 });
    Object.defineProperty(tip, "offsetHeight", {
      value: 60,
      configurable: true,
    });
    Object.defineProperty(tip, "offsetWidth", {
      value: 240,
      configurable: true,
    });
    wrap.getBoundingClientRect = at(110);
    fireEvent.focusIn(tag);
    expect(tip).toBeVisible();
    expect(tip).toHaveClass("below");
    fireEvent.keyDown(tag, { key: "Escape" });
    // With room above, it opens above, as the approved screens draw it.
    wrap.getBoundingClientRect = at(400);
    fireEvent.focusIn(tag);
    expect(tip).toBeVisible();
    expect(tip).not.toHaveClass("below");
  });

  it("closes on Escape when opened by hover, wherever focus is", async () => {
    figured();
    open("/ask/t1");
    const answer = await screen.findByRole("article", { name: "Answer" });
    const tag = within(answer).getByRole("button", {
      name: /Not in its sources/,
    });
    const tip = screen.getByRole("tooltip", { hidden: true });
    screen.getByRole("button", { name: "Copy" }).focus();
    await userEvent.hover(tag);
    expect(tip).toBeVisible();
    await userEvent.keyboard("{Escape}");
    expect(tip).not.toBeVisible();
  });
});
