// Builds SecureFolio.app, ad-hoc signed: the UI, the standalone Python with the app
// group installed, our code and the bridge. Needs no network once installed.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  cpSync,
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { platform } from "node:process";
import { fileURLToPath } from "node:url";

import { scanBuiltApp } from "./app-text.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CACHE = join(ROOT, ".cache/bundle");
const PYTHON = join(CACHE, "python");
const CODE = join(CACHE, "code");
const PACKAGES = ["backend", "agent", "worker"];
const APP = join(
  ROOT,
  "desktop/src-tauri/target/release/bundle/macos/SecureFolio.app",
);

function run(command, args, options = {}) {
  execFileSync(command, args, { cwd: ROOT, stdio: "inherit", ...options });
}

function capture(command, args) {
  return execFileSync(command, args, { cwd: ROOT, encoding: "utf8" }).trim();
}

function preparePython() {
  const lock = createHash("sha256")
    .update(readFileSync(join(ROOT, "uv.lock")))
    .digest("hex");
  const stamp = join(CACHE, "python.stamp");
  if (existsSync(stamp) && readFileSync(stamp, "utf8") === lock) return;
  rmSync(PYTHON, { recursive: true, force: true });
  const interpreter = capture("uv", [
    "python",
    "find",
    "--managed-python",
    "--no-project",
    "--offline",
  ]);
  const home = dirname(dirname(realpathSync(interpreter)));
  cpSync(home, PYTHON, { recursive: true, verbatimSymlinks: true });
  const requirements = join(CACHE, "requirements.txt");
  run("uv", [
    "export",
    "--frozen",
    "--offline",
    "--no-default-groups",
    "--group",
    "app",
    "--no-hashes",
    "--no-emit-project",
    "-o",
    requirements,
    "-q",
  ]);
  run("uv", [
    "pip",
    "install",
    "--offline",
    "--python",
    join(PYTHON, "bin/python3"),
    "--break-system-packages",
    "--no-deps",
    "-q",
    "-r",
    requirements,
  ]);
  writeFileSync(stamp, lock);
}

function prepareCode() {
  rmSync(CODE, { recursive: true, force: true });
  for (const name of PACKAGES) {
    cpSync(join(ROOT, name), join(CODE, name), {
      recursive: true,
      filter: (source) =>
        !source.includes("__pycache__") && !source.endsWith("/tests"),
    });
  }
  writeFileSync(join(CODE, "backend/RELEASE"), "");
  // Compiled now, as the running app mustn't write into its own signed bundle.
  run(join(PYTHON, "bin/python3"), [
    "-m",
    "compileall",
    "-q",
    "--invalidation-mode",
    "unchecked-hash",
    CODE,
  ]);
}

function buildBridge() {
  const manifest = join(ROOT, "desktop/mcp-bridge/Cargo.toml");
  run("cargo", [
    "build",
    "--release",
    "--offline",
    "--manifest-path",
    manifest,
  ]);
  const binary = join(
    ROOT,
    "desktop/mcp-bridge/target/release/securefolio-mcp",
  );
  cpSync(binary, join(CACHE, "securefolio-mcp"));
  const stage = join(CACHE, "mcpb");
  rmSync(stage, { recursive: true, force: true });
  mkdirSync(join(stage, "server"), { recursive: true });
  cpSync(
    join(ROOT, "desktop/mcp-bridge/manifest.json"),
    join(stage, "manifest.json"),
  );
  cpSync(binary, join(stage, "server/securefolio-mcp"));
  // A .mcpb is a zip with the manifest at its root.
  rmSync(join(CACHE, "securefolio.mcpb"), { force: true });
  run("zip", ["-qr", join(CACHE, "securefolio.mcpb"), "."], { cwd: stage });
}

function signAdHoc() {
  // Inside out would matter for notarizing (scripts/sign-release.mjs); ad hoc only
  // needs every nested binary to carry a signature.
  run("codesign", [
    "--force",
    "--deep",
    "--sign",
    "-",
    "--entitlements",
    join(ROOT, "desktop/src-tauri/SecureFolio.entitlements"),
    APP,
  ]);
  run("codesign", ["--verify", "--deep", "--strict", APP]);
}

// No screen says "Claude" where any provider could answer: Claude Desktop, Claude
// Code and Claude's model names aside.
function checkAppText() {
  const found = scanBuiltApp(join(ROOT, "app/dist"));
  if (found.length > 0) {
    console.error(
      `The built app says "Claude" where any provider could answer:\n${found.join("\n")}`,
    );
    process.exit(1);
  }
}

if (platform !== "darwin") {
  console.log("The bundle is built only on macOS.");
} else {
  mkdirSync(CACHE, { recursive: true });
  run("npx", ["vite", "build", "app"]);
  checkAppText();
  preparePython();
  prepareCode();
  buildBridge();
  run(
    "npx",
    [
      "tauri",
      "build",
      "--bundles",
      "app",
      "--config",
      "src-tauri/tauri.bundle.json",
    ],
    {
      cwd: join(ROOT, "desktop"),
      env: { ...process.env, CARGO_NET_OFFLINE: "true" },
    },
  );
  signAdHoc();
  console.log(`Built ${APP}`);
}
