import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

function runtimeFile(): string {
  // DOCSYNTH_VAULT: a development vault named before the rename.
  const vault =
    process.env.SECUREFOLIO_VAULT ??
    process.env.DOCSYNTH_VAULT ??
    join(homedir(), "Library/Application Support/SecureFolio");
  return join(vault, "runtime.json");
}

// The dev server hands the page the backend's address and token. A build has no
// such route: the desktop shell supplies them through a Tauri command.
function devConnection(): Plugin {
  return {
    name: "securefolio-dev-connection",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use("/__connection", (_request, response) => {
        const runtime = JSON.parse(readFileSync(runtimeFile(), "utf8")) as {
          port: number;
          app_token: string;
        };
        response.setHeader("Content-Type", "application/json");
        response.end(
          JSON.stringify({
            base: `http://127.0.0.1:${String(runtime.port)}`,
            token: runtime.app_token,
          }),
        );
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), devConnection()],
  // The app needs nothing from .env, and the tests must never read it.
  envDir: false,
  build: { target: "safari16" },
  test: {
    environment: "jsdom",
    setupFiles: ["src/setupTests.ts"],
    server: { deps: { inline: [/@tauri-apps/] } },
    coverage: {
      include: ["src/**"],
      exclude: ["src/main.tsx", "src/**/*.test.{ts,tsx}", "src/setupTests.ts"],
      thresholds: { lines: 80, functions: 80, branches: 80, statements: 80 },
    },
  },
});
