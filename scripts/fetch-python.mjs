// Installing's network step for the bundle and the Rust checks: uv fetches and
// verifies the python-build-standalone build pinned by .python-version, and cargo
// fetches every crate. After this, the checks and the bundle need no network.
import { execFileSync } from "node:child_process";
import { platform } from "node:process";

const crates = ["desktop/mcp-bridge/Cargo.toml"];
if (platform === "darwin") {
  crates.push("desktop/src-tauri/Cargo.toml");
  execFileSync("uv", ["python", "install", "--managed-python"], {
    stdio: "inherit",
  });
}
for (const manifest of crates) {
  execFileSync("cargo", ["fetch", "--locked", "--manifest-path", manifest], {
    stdio: "inherit",
  });
}
