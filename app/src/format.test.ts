import { describe, expect, it } from "vitest";

import { modelName } from "./format";

describe("modelName", () => {
  it("names a cloud model with or without its provider, and leaves a local tag alone", () => {
    expect(modelName("anthropic:claude-sonnet-5-5")).toBe("Claude Sonnet 5.5");
    expect(modelName("claude-opus-5-5")).toBe("Claude Opus 5.5");
    expect(modelName("openai:gpt-6.1")).toBe("gpt-6.1");
    expect(modelName("qwen3.5:27b")).toBe("qwen3.5:27b");
  });
});
