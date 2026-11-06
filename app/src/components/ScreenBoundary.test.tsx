import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ScreenBoundary } from "./ScreenBoundary";

let broken = true;

function Screen(): string {
  if (broken) throw new Error("answer.parts is undefined");
  return "The screen";
}

beforeEach(() => {
  broken = true;
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

it("says what failed instead of emptying the window, and draws the screen again elsewhere", () => {
  const { rerender } = render(
    <ScreenBoundary place="/ask/t1">
      <Screen />
    </ScreenBoundary>,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Something on it failed: answer.parts is undefined",
  );
  broken = false;
  rerender(
    <ScreenBoundary place="/ask/t1">
      <Screen />
    </ScreenBoundary>,
  );
  expect(screen.getByRole("alert")).toBeVisible();
  rerender(
    <ScreenBoundary place="/home">
      <Screen />
    </ScreenBoundary>,
  );
  expect(screen.getByText("The screen")).toBeVisible();
});

it("tries the same screen again on request", async () => {
  render(
    <ScreenBoundary place="/ask/t1">
      <Screen />
    </ScreenBoundary>,
  );
  broken = false;
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(screen.getByText("The screen")).toBeVisible();
});
