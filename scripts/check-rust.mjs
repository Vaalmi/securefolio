// The Rust checks: the bridge everywhere, and the desktop shell on macOS.
import { execFileSync } from "node:child_process";
import { platform } from "node:process";

const crates = ["desktop/mcp-bridge"];
if (platform === "darwin") crates.push("desktop/src-tauri");

for (const crate of crates) {
  const manifest = `${crate}/Cargo.toml`;
  const run = (command, ...args) => {
    execFileSync("cargo", [command, "--manifest-path", manifest, ...args], {
      stdio: "inherit",
      env: { ...process.env, CARGO_NET_OFFLINE: "true" },
    });
  };
  run("fmt", "--check");
  run("clippy", "--all-targets", "--", "-D", "warnings");
  run("test");
}
