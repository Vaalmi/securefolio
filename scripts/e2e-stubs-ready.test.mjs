// The e2e backend starts only once the stand-in models listen. Started earlier, its
// first "who would answer" finds no cloud provider, remembers that for 30 s, and the
// answers in that time come from the local model (journey 4 got 1 citation, not 3).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { stubPorts, waitForPorts } from "../e2e/stubs-ready.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

function freePort() {
  return new Promise((resolve) => {
    const server = createServer().listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

test("the wait ends only once a stand-in that starts late listens", async () => {
  const port = await freePort();
  let listeningAt = 0;
  const server = createServer();
  setTimeout(() => {
    server.listen(port, "127.0.0.1", () => {
      listeningAt = Date.now();
    });
  }, 300);
  try {
    await waitForPorts([port], 10);
    assert.ok(listeningAt > 0, "the wait ended before anything listened");
  } finally {
    server.close();
  }
});

test("the wait gives up when nothing listens", async () => {
  const port = await freePort();
  await assert.rejects(waitForPorts([port], 0.3), /Nothing listens on port/);
});

test("the ports are read from the stand-ins' URLs", () => {
  assert.deepEqual(
    stubPorts({
      SECUREFOLIO_STUB_ANTHROPIC_URL: "http://127.0.0.1:8101",
      SECUREFOLIO_STUB_OLLAMA_URL: "http://127.0.0.1:8102",
      SECUREFOLIO_VAULT: "e2e/.vault",
    }),
    [8101, 8102],
  );
});

test("the launcher waits for the stand-ins before it starts the backend", () => {
  const launcher = readFileSync(join(ROOT, "e2e/start-backend.mjs"), "utf8");
  const stubs = launcher.indexOf('"testing.e2e_stubs"');
  const wait = launcher.indexOf("await waitForPorts(stubPorts(env))");
  const backend = launcher.indexOf('"backend.main"');
  assert.ok(stubs >= 0 && wait >= 0 && backend >= 0);
  assert.ok(
    stubs < wait && wait < backend,
    "the backend starts before the wait",
  );
});
