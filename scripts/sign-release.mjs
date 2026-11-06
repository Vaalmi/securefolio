// A maintainer's step: signs SecureFolio.app inside out with a Developer ID and submits
// it for notarization. Needs CODESIGN_IDENTITY and a notarytool profile in
// NOTARY_PROFILE. The checks never run it.
import { execFileSync } from "node:child_process";
import { readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { env, exit } from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const APP = join(
  ROOT,
  "desktop/src-tauri/target/release/bundle/macos/SecureFolio.app",
);
const ENTITLEMENTS = join(ROOT, "desktop/src-tauri/SecureFolio.entitlements");

const identity = env.CODESIGN_IDENTITY;
const profile = env.NOTARY_PROFILE;
if (!identity || !profile) {
  console.error("Set CODESIGN_IDENTITY and NOTARY_PROFILE first.");
  exit(1);
}

function sign(path) {
  execFileSync("codesign", [
    "--force",
    "--options",
    "runtime",
    "--timestamp",
    "--entitlements",
    ENTITLEMENTS,
    "--sign",
    identity,
    path,
  ]);
}

function isBinary(path) {
  if (/\.(so|dylib)$/.test(path)) return true;
  return (statSync(path).mode & 0o111) !== 0 && !/\.(py|sh|txt)$/.test(path);
}

function signTree(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) signTree(path);
    else if (isBinary(path)) sign(path);
  }
}

signTree(join(APP, "Contents/Resources"));
sign(APP);
execFileSync("codesign", ["--verify", "--deep", "--strict", APP], {
  stdio: "inherit",
});
const zip = `${APP}.zip`;
execFileSync("ditto", ["-c", "-k", "--keepParent", APP, zip]);
execFileSync(
  "xcrun",
  ["notarytool", "submit", zip, "--keychain-profile", profile, "--wait"],
  { stdio: "inherit" },
);
execFileSync("xcrun", ["stapler", "staple", APP], { stdio: "inherit" });
