import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { calls } from "../../fakeBackend";
import { installBrowser, open } from "../../testApp";
import { dismissToast } from "../../toast";
import { backend, key } from "./keysFixtures";

// Settings, Keys: an answer counts only for the key and ID it was for.

beforeEach(() => {
  installBrowser();
});

describe("Keys, a late answer", () => {
  // An answer counts only for what it tested.
  function slowTest() {
    let answer: ((value: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    return {
      route: () => pending,
      answer: (body: object, status = 200) => {
        answer?.(new Response(JSON.stringify(body), { status }));
      },
    };
  }

  it("keeps a key typed while the last one was tested", async () => {
    const slow = slowTest();
    backend([key("openai")], { "POST /settings/keys/openai/test": slow.route });
    open("/settings/models");
    await userEvent.click(
      await screen.findByRole("button", { name: "Add key" }),
    );
    const field = screen.getByLabelText("OpenAI API key");
    await userEvent.type(field, "sk-first");
    await userEvent.click(
      screen.getByRole("button", { name: "Test and save" }),
    );
    await userEvent.type(field, "-second");
    slow.answer(key("openai", { present: true, hint: "••••irst" }));
    await waitFor(() => {
      const reloads = calls.filter(
        (c) => c.method === "GET" && c.path === "/settings/keys",
      );
      expect(reloads.length).toBeGreaterThan(1); // the saved key is shown
    });
    expect(screen.getByLabelText("OpenAI API key")).toHaveValue(
      "sk-first-second",
    );
    expect(
      screen.queryByText(/Key works. Saved to your Keychain./),
    ).not.toBeInTheDocument();
  });

  it("keeps an ID typed while the saved key was tested", async () => {
    const slow = slowTest();
    backend(
      [key("openai", { present: true, hint: "••••Rm4c", needs_id: true })],
      {
        "POST /settings/keys/openai/test": slow.route,
      },
    );
    open("/settings/models");
    const field = await screen.findByLabelText("Project ID (optional)");
    await userEvent.type(field, "proj_a");
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    await userEvent.type(field, "b");
    slow.answer({});
    await waitFor(() => {
      const reloads = calls.filter(
        (c) => c.method === "GET" && c.path === "/settings/keys",
      );
      expect(reloads.length).toBeGreaterThan(1);
    });
    expect(screen.getByLabelText("Project ID (optional)")).toHaveValue(
      "proj_ab",
    );
    expect(screen.queryByText("OpenAI key works.")).not.toBeInTheDocument();
  });

  it("keeps an ID typed while the last one was saved", async () => {
    // The same for Save.
    dismissToast(); // toasts are global: an earlier test's may still show
    const slow = slowTest();
    backend(
      [
        key("openai", {
          present: true,
          hint: "••••Rm4c",
          scope_id: "proj_old",
        }),
      ],
      { "PUT /settings/keys/openai/scope": slow.route },
    );
    open("/settings/models");
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Change the OpenAI Project ID",
      }),
    );
    const field = screen.getByLabelText("Project ID (optional)");
    await userEvent.clear(field);
    await userEvent.type(field, "proj_a");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await userEvent.type(field, "b");
    slow.answer(key("openai", { present: true, scope_id: "proj_a" }));
    await waitFor(() => {
      const reloads = calls.filter(
        (c) => c.method === "GET" && c.path === "/settings/keys",
      );
      expect(reloads.length).toBeGreaterThan(1);
    });
    expect(screen.getByLabelText("Project ID (optional)")).toHaveValue(
      "proj_ab",
    );
    expect(
      screen.queryByText("OpenAI Project ID saved."),
    ).not.toBeInTheDocument();
  });

  it("drops a test's answer once its key is removed", async () => {
    // A late needs-ID answer left the removed key's row with no Add key.
    dismissToast();
    const slow = slowTest();
    let rows = [key("openai", { present: true, hint: "••••Rm4c" })];
    backend([], {
      "GET /settings/keys": () => rows,
      "POST /settings/keys/openai/test": slow.route,
      "DELETE /settings/keys/openai": () => {
        rows = [key("openai")];
        return rows[0];
      },
    });
    open("/settings/models");
    await userEvent.click(await screen.findByRole("button", { name: "Test" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Remove the OpenAI key" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Remove key" }));
    const add = await screen.findByRole("button", { name: "Add key" });
    slow.answer(
      {
        detail:
          "This key isn't tied to one project. Add its Project ID, then Test again.",
      },
      422,
    );
    await waitFor(() => {
      const reloads = calls.filter(
        (c) => c.method === "GET" && c.path === "/settings/keys",
      );
      expect(reloads.length).toBeGreaterThan(2); // after the removal, and the answer
    });
    expect(add).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add key" })).toBeVisible();
    expect(
      screen.queryByText(/isn't tied to one project/),
    ).not.toBeInTheDocument();
  });
});
