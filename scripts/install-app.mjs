// `npm run app`: builds SecureFolio.app and puts it in /Applications in place of the
// one there, so there's one copy to open. A running SecureFolio is asked to quit
// first and is opened again after. The library in
// ~/Library/Application Support/SecureFolio is never touched.
//
// The new build is first staged beside the installed app as `.SecureFolio.app.incoming`
// and its signature checked there; only then do two renames on the same disk swap it
// in, keeping the old app as `.SecureFolio.app.previous` until the swap is done. A
// later run that finds those left by an interrupted one recovers first: a previous
// app with nothing good in place goes back, and a stray incoming copy is removed.
//
//   npm run app [-- --no-build]
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, realpathSync, renameSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { argv, exit, platform } from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const BUILT = join(
  ROOT,
  "desktop/src-tauri/target/release/bundle/macos/SecureFolio.app",
);
export const INSTALLED = "/Applications/SecureFolio.app";
const LSREGISTER =
  "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister";
const POLL_MS = 250;
const SHELL_SECONDS = 6;
const BACKEND_SECONDS = 20;

const incomingOf = (target) =>
  join(dirname(target), ".SecureFolio.app.incoming");
const previousOf = (target) =>
  join(dirname(target), ".SecureFolio.app.previous");

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

/** The pids in `ps -axww -o pid=,comm=` output whose executable is inside the app
 * (or the part of it named, such as "MacOS/"), matched as a path, never a pattern. */
export function pidsIn(listing, app, part = "") {
  const inside = `${app}/Contents/${part}`;
  const pids = [];
  for (const line of listing.split("\n")) {
    const trimmed = line.trimStart();
    const gap = trimmed.indexOf(" ");
    if (gap <= 0) continue;
    const pid = Number(trimmed.slice(0, gap));
    const executable = trimmed.slice(gap).trimStart();
    if (Number.isInteger(pid) && executable.startsWith(inside)) pids.push(pid);
  }
  return pids;
}

/** The pids of processes running from inside this app bundle. A process list that
 * can't be read stops the install rather than reading as "not running". */
export function processesIn(app, part = "", list = listProcesses) {
  const real = existsSync(app) ? realpathSync(app) : app;
  const listing = list();
  return [
    ...new Set([...pidsIn(listing, app, part), ...pidsIn(listing, real, part)]),
  ];
}

function listProcesses() {
  const run = spawnSync("ps", ["-axww", "-o", "pid=,comm="], {
    encoding: "utf8",
  });
  if (run.status !== 0) {
    throw new Error(`Couldn't read the process list: ${run.stderr.trim()}`);
  }
  return run.stdout;
}

function stop(pids) {
  for (const pid of pids) {
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      // gone already
    }
  }
}

function waitUntilGone(app, seconds) {
  const until = Date.now() + seconds * 1000;
  while (Date.now() < until) {
    if (processesIn(app).length === 0) return true;
    sleep(POLL_MS);
  }
  return processesIn(app).length === 0;
}

/** Asks a running SecureFolio to quit: the shell first, then whatever is left in the
 * bundle, so the backend stops the worker and removes its runtime file. Whether it
 * was running. */
export function quit(app) {
  if (processesIn(app).length === 0) return false;
  stop(processesIn(app, "MacOS/"));
  if (!waitUntilGone(app, SHELL_SECONDS)) {
    stop(processesIn(app));
    if (!waitUntilGone(app, BACKEND_SECONDS)) {
      throw new Error(
        `SecureFolio didn't quit within ${String(BACKEND_SECONDS)} s; nothing was changed. Quit it from the menu bar and run this again.`,
      );
    }
  }
  return true;
}

/** A rename, or on another disk a copy that keeps the bundle's signature and
 * attributes. The source is left for the caller. */
function stage(from, to) {
  try {
    renameSync(from, to);
  } catch (error) {
    if (!(
      error instanceof Error &&
      "code" in error &&
      error.code === "EXDEV"
    )) {
      throw error;
    }
    execFileSync("ditto", [from, to]);
  }
}

function codesignVerify(app) {
  execFileSync("codesign", ["--verify", "--deep", "--strict", app]);
}

function verifies(app, verify) {
  try {
    verify(app);
    return true;
  } catch {
    return false;
  }
}

/** Cleans up after an install that was cut short: a stray incoming copy is removed,
 * and a kept previous app goes back if nothing good is in place, or is removed if a
 * good app is. Whether it had to quit a running copy to do so. */
export function recover(target, verify = codesignVerify, stopped = stopFrom) {
  rmSync(incomingOf(target), { recursive: true, force: true });
  const previous = previousOf(target);
  if (!existsSync(previous)) return false;
  if (existsSync(target) && verifies(target, verify)) {
    const wasRunning = stopped(previous);
    rmSync(previous, { recursive: true, force: true });
    return wasRunning;
  }
  const wasRunning = existsSync(target) && stopped(target);
  rmSync(target, { recursive: true, force: true });
  renameSync(previous, target);
  return wasRunning;
}

/** Before a bundle is removed: anything still running from it quits, or the install
 * stops with nothing removed. Whether it was running, so it's opened again after. */
function stopFrom(app) {
  return processesIn(app).length > 0 && quit(app);
}

/** Stages and verifies the build beside target, then swaps it in by renames on that
 * disk. The old app is back in place if anything fails. */
export function replaceApp(
  built,
  target,
  verify = codesignVerify,
  beforeSwap = quit,
) {
  if (!existsSync(built)) throw new Error(`No build at ${built}.`);
  recover(target, verify);
  const incoming = incomingOf(target);
  const previous = previousOf(target);
  stage(built, incoming);
  try {
    verify(incoming);
  } catch (error) {
    rmSync(incoming, { recursive: true, force: true });
    throw error;
  }
  // Anything opened again while the build was staged quits now, just before the two
  // renames, so no app runs from the one being retired.
  beforeSwap(target);
  const hadOne = existsSync(target);
  if (hadOne) renameSync(target, previous);
  try {
    renameSync(incoming, target);
  } catch (error) {
    if (hadOne) renameSync(previous, target);
    throw error;
  }
  rmSync(built, { recursive: true, force: true });
  return hadOne ? previous : null;
}

function register(target) {
  // Forget the build folder's copy, so macOS opens only the installed one by name.
  spawnSync(LSREGISTER, ["-u", BUILT]);
  spawnSync(LSREGISTER, ["-f", target]);
}

/** Removes the old app, unless something still runs from it; a later run removes it
 * then (recover). */
function retire(previous) {
  if (processesIn(previous).length > 0) {
    console.error(
      `${previous} is still in use, so it stays until the next install.`,
    );
    return;
  }
  rmSync(previous, { recursive: true, force: true });
}

function build() {
  execFileSync("node", [join(ROOT, "scripts/bundle.mjs")], {
    cwd: ROOT,
    stdio: "inherit",
  });
}

function install(fromBuild) {
  let wasRunning = fromBuild;
  try {
    wasRunning = quit(INSTALLED) || wasRunning;
    // One opened while the build was staged is quit just before the swap, and
    // opened again after like any other.
    const previous = replaceApp(BUILT, INSTALLED, codesignVerify, (target) => {
      wasRunning = quit(target) || wasRunning;
    });
    // Anything opened again during the swap runs the new app, from INSTALLED.
    register(INSTALLED);
    if (previous) retire(previous);
    console.log(`Installed ${INSTALLED}.`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  } finally {
    // Whichever app is in place now, new or the old one kept, opens again.
    if (
      wasRunning &&
      existsSync(INSTALLED) &&
      processesIn(INSTALLED).length === 0
    ) {
      execFileSync("open", [INSTALLED]);
      console.log("Opened SecureFolio again.");
    }
  }
}

function main() {
  const { values } = parseArgs({
    args: argv.slice(2),
    options: { "no-build": { type: "boolean", default: false } },
  });
  if (platform !== "darwin") {
    console.error("SecureFolio.app is built and installed only on macOS.");
    exit(1);
  }
  let wasRunning = false;
  const reopenAndFail = (message) => {
    console.error(message);
    if (wasRunning && existsSync(INSTALLED)) execFileSync("open", [INSTALLED]);
    exit(1);
  };
  try {
    // First what an interrupted run left, so a good app is in place before a build.
    // An app it had to quit, or one opened from the build folder (closed before its
    // bundle is rebuilt or moved), is opened again, installed, after; or, if the
    // build fails, whichever app is installed is.
    wasRunning = recover(INSTALLED);
    wasRunning = (existsSync(BUILT) && quit(BUILT)) || wasRunning;
    if (!values["no-build"]) build();
  } catch (error) {
    reopenAndFail(error instanceof Error ? error.message : String(error));
  }
  if (!existsSync(BUILT)) {
    reopenAndFail(`No build at ${BUILT}: run npm run app without --no-build.`);
  }
  install(wasRunning);
}

/** Whether the script at this path is the module at this URL: Node runs a module
 * from its real path, so a path through a link (macOS's /var) or with spaces counts. */
export function isMain(moduleUrl, scriptPath) {
  return (
    Boolean(scriptPath) &&
    moduleUrl === pathToFileURL(realpathSync(scriptPath)).href
  );
}

if (isMain(import.meta.url, argv[1])) main();
