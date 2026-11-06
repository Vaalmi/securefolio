// Runs the backend (which starts the worker) and the app's Vite server against
// the development vault, on fixed local ports.
import { spawn } from "node:child_process";

const VITE = 5173;
const env = {
  ...process.env,
  SECUREFOLIO_DEV_ORIGINS: `http://127.0.0.1:${String(VITE)},http://localhost:${String(VITE)}`,
};
const children = [
  spawn("uv", ["run", "--env-file", ".env", "-m", "backend.main"], {
    stdio: "inherit",
    env,
  }),
  spawn("npx", ["vite", "app", "--host", "127.0.0.1", "--port", String(VITE)], {
    stdio: "inherit",
    // DOCSYNTH_VAULT: a development vault named before the rename.
    env: {
      ...env,
      SECUREFOLIO_VAULT:
        env.SECUREFOLIO_VAULT ?? env.DOCSYNTH_VAULT ?? "./.vault-dev",
    },
  }),
];
const stop = () => {
  for (const child of children) child.kill("SIGTERM");
};
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
for (const child of children) child.on("exit", stop);
