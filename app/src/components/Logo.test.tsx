import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { fakeBackend } from "../fakeBackend";
import { installBrowser, open } from "../testApp";

// The logo on its indigo square beside "SecureFolio", in the sidebar and on first run, as
// decoration.

beforeEach(() => {
  installBrowser();
});

function logos(): HTMLImageElement[] {
  return Array.from(
    document.querySelectorAll<HTMLImageElement>("img.logo-tile"),
  );
}

describe("the logo", () => {
  it("sits beside SecureFolio in the sidebar", async () => {
    fakeBackend({});
    open("/home");
    await screen.findByRole("combobox");
    const [logo] = logos();
    expect(logo.getAttribute("src")).toMatch(/securefolio_logo_bg\.svg/);
    expect(logo).toHaveAttribute("alt", "");
    expect(logo).toHaveAttribute("aria-hidden", "true");
    expect(logo).toHaveAttribute("width", "22");
    expect(logo.closest(".sb-brand")).toHaveTextContent("SecureFolio");
  });

  it("and on first run", async () => {
    fakeBackend({});
    open("/setup/key");
    await screen.findByRole("heading", { name: "Connect a cloud model" });
    const [logo] = logos();
    expect(logo.getAttribute("src")).toMatch(/securefolio_logo_bg\.svg/);
    expect(logo).toHaveAttribute("width", "28");
    expect(logo.parentElement).toHaveTextContent("SecureFolio");
  });
});
