import { describe, expect, it } from "vitest";

import { placeOf, placeText } from "./reportPlace";

// The place field reads what the owner types into the place the backend checks,
// and starts from a citation's place in the same words.

describe("the place a report names", () => {
  it("reads lines however the dash and spaces fall", () => {
    expect(placeOf("lines", "13–14")).toEqual({
      kind: "lines",
      start: 13,
      end: 14,
    });
    expect(placeOf("lines", " 13 - 14 ")).toEqual({
      kind: "lines",
      start: 13,
      end: 14,
    });
    expect(placeOf("lines", "7")).toEqual({ kind: "lines", start: 7, end: 7 });
    expect(placeOf("lines", "lines")).toBeNull();
    expect(placeOf("lines", "2.5–3")).toBeNull();
    expect(placeOf("lines", "3–2")).toBeNull();
    expect(placeOf("lines", "1-2-3")).toBeNull();
  });

  it("reads a sheet whose name holds a comma by its last one", () => {
    expect(placeOf("cells", "Bills, 2026, B4:F4")).toEqual({
      kind: "cells",
      sheet: "Bills, 2026",
      cells: "B4:F4",
    });
    expect(placeOf("cells", "B4:F4")).toBeNull();
  });

  it("reads a page, slide or message by a whole number", () => {
    expect(placeOf("page", "6")).toEqual({ kind: "page", page: 6 });
    expect(placeOf("slide", "2")).toEqual({ kind: "slide", slide: 2 });
    expect(placeOf("message", "3")).toEqual({ kind: "message", message: 3 });
    expect(placeOf("page", "0")).toBeNull();
    expect(placeOf("page", "2.5")).toBeNull();
  });

  it("starts from a citation's place in the same words", () => {
    expect(
      placeText({
        kind: "lines",
        start: 13,
        end: 14,
        quote_lines: [],
      } as never),
    ).toBe("13–14");
    expect(
      placeText({ kind: "sheet", sheet: "Bills", range: "B4:F4" } as never),
    ).toBe("Bills, B4:F4");
    expect(placeText(undefined)).toBe("");
  });
});
