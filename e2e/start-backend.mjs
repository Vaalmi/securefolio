// Builds the tests' vault from data/library, then starts the backend (and through it
// a worker with stand-in models).
import { spawn, spawnSync } from "node:child_process";
import { rmSync } from "node:fs";
import { resolve } from "node:path";

const VAULT = "e2e/.vault";
const IN_THE_CLOUD = resolve("data/library/Scans/In the cloud.pdf");
const env = {
  ...process.env,
  SECUREFOLIO_VAULT: VAULT,
  SECUREFOLIO_PORT: "8100",
  SECUREFOLIO_DEV_ORIGINS: "http://127.0.0.1:5199",
  SECUREFOLIO_STUB_MODELS: "1",
  SECUREFOLIO_WORKER_MODULE: "testing.e2e_worker",
  SECUREFOLIO_DATALESS_PATHS: IN_THE_CLOUD,
  SECUREFOLIO_STUB_ANTHROPIC_URL: "http://127.0.0.1:8101",
  SECUREFOLIO_STUB_OLLAMA_URL: "http://127.0.0.1:8102",
  SECUREFOLIO_STUB_OPENAI_URL: "http://127.0.0.1:8103",
  SECUREFOLIO_TEST_KEY: "test-not-a-real-key",
};
rmSync(VAULT, { recursive: true, force: true });
rmSync("e2e/.fixtures", { recursive: true, force: true });
const seeded = spawnSync("uv", ["run", "-m", "testing.e2e_seed"], {
  stdio: "inherit",
  env,
});
if (seeded.status !== 0) process.exit(seeded.status ?? 1);
const stubs = spawn("uv", ["run", "-m", "testing.e2e_stubs"], {
  stdio: "inherit",
  env,
});
const backend = spawn("uv", ["run", "-m", "backend.main"], {
  stdio: "inherit",
  env,
});
const stop = () => {
  stubs.kill("SIGTERM");
  backend.kill("SIGTERM");
};
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
backend.on("exit", (code) => {
  stubs.kill("SIGTERM");
  process.exit(code ?? 0);
});
