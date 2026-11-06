// Tests of installing the built app: the new build takes the old one's place only
// once it's staged and verified, a failure or an interrupted run leaves a good app in
// place, and the running app is found by its executable's path, nothing looser.
import assert from "node:assert/strict";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { pathToFileURL } from "node:url";

import {
  isMain,
  pidsIn,
  processesIn,
  recover,
  replaceApp,
} from "./install-app.mjs";

function app(at, version) {
  mkdirSync(join(at, "Contents"), { recursive: true });
  writeFileSync(join(at, "Contents/version"), version);
}

const versionOf = (at) => readFileSync(join(at, "Contents/version"), "utf8");
const passes = () => undefined;
const nothingRuns = () => false;
const failsFor = (bad) => (at) => {
  if (versionOf(at) === bad) throw new Error(`invalid signature: ${bad}`);
};

function folders() {
  const root = mkdtempSync(join(tmpdir(), "install-app-"));
  const apps = join(root, "Applications");
  mkdirSync(apps);
  return {
    root,
    built: join(root, "build", "SecureFolio.app"),
    target: join(apps, "SecureFolio.app"),
    incoming: join(apps, ".SecureFolio.app.incoming"),
    previous: join(apps, ".SecureFolio.app.previous"),
  };
}

function inFolders(body) {
  const place = folders();
  try {
    body(place);
  } finally {
    rmSync(place.root, { recursive: true });
  }
}

test("a first install moves the build into place", () => {
  inFolders(({ built, target, incoming }) => {
    app(built, "new");
    assert.equal(replaceApp(built, target, passes, nothingRuns), null);
    assert.equal(versionOf(target), "new");
    assert.equal(existsSync(built), false);
    assert.equal(existsSync(incoming), false);
  });
});

test("a new build replaces the installed one, which is kept until the swap is done", () => {
  inFolders(({ built, target, previous }) => {
    app(target, "old");
    app(built, "new");
    assert.equal(replaceApp(built, target, passes, nothingRuns), previous);
    assert.equal(versionOf(target), "new");
    assert.equal(versionOf(previous), "old");
    assert.equal(existsSync(built), false);
  });
});

test("a build that fails its signature check never reaches the installed place", () => {
  inFolders(({ built, target, incoming, previous }) => {
    app(target, "old");
    app(built, "new");
    assert.throws(
      () => replaceApp(built, target, failsFor("new"), nothingRuns),
      /invalid signature/,
    );
    assert.equal(versionOf(target), "old");
    assert.equal(existsSync(incoming), false);
    assert.equal(existsSync(previous), false);
  });
});

test("a copy opened while the build was staged is quit just before the swap", () => {
  inFolders(({ built, target }) => {
    app(target, "old");
    app(built, "new");
    const quitAt = [];
    replaceApp(built, target, passes, (at) => {
      quitAt.push(versionOf(at));
      return true;
    });
    assert.deepEqual(quitAt, ["old"]); // the installed app, before it was moved
  });
});

test("an interrupted install with a broken app in place puts the kept one back", () => {
  inFolders(({ target, incoming, previous }) => {
    app(target, "partial");
    app(previous, "good");
    app(incoming, "half-copied");
    recover(target, failsFor("partial"));
    assert.equal(versionOf(target), "good");
    assert.equal(existsSync(previous), false);
    assert.equal(existsSync(incoming), false);
  });
});

test("an interrupted install with nothing in place puts the kept one back", () => {
  inFolders(({ target, previous }) => {
    app(previous, "good");
    recover(target, passes);
    assert.equal(versionOf(target), "good");
  });
});

test("a kept app something still runs from is stopped before it's removed", () => {
  inFolders(({ target, previous }) => {
    app(target, "new");
    app(previous, "old");
    const stopped = [];
    const wasRunning = recover(target, passes, (at) => {
      stopped.push(versionOf(at));
      return true;
    });
    assert.deepEqual(stopped, ["old"]);
    assert.equal(wasRunning, true); // so the install opens SecureFolio again
    assert.equal(existsSync(previous), false);
  });
});

test("a kept app that won't stop is left in place", () => {
  inFolders(({ target, previous }) => {
    app(target, "new");
    app(previous, "old");
    assert.throws(
      () =>
        recover(target, passes, () => {
          throw new Error("didn't quit");
        }),
      /didn't quit/,
    );
    assert.equal(versionOf(previous), "old");
    assert.equal(versionOf(target), "new");
  });
});

test("a broken app in place that was running is opened again after recovery", () => {
  inFolders(({ target, previous }) => {
    app(target, "partial");
    app(previous, "good");
    const wasRunning = recover(
      target,
      failsFor("partial"),
      (at) => versionOf(at) === "partial",
    );
    assert.equal(wasRunning, true);
    assert.equal(versionOf(target), "good");
  });
});

test("recovery with nothing running reports nothing to reopen", () => {
  inFolders(({ target, previous }) => {
    app(target, "new");
    app(previous, "old");
    assert.equal(
      recover(target, passes, () => false),
      false,
    );
  });
});

test("an install cut short after the swap only tidies up", () => {
  inFolders(({ target, previous }) => {
    app(target, "new");
    app(previous, "old");
    recover(target, passes);
    assert.equal(versionOf(target), "new");
    assert.equal(existsSync(previous), false);
  });
});

test("a kept app is recovered before a new build fails, never lost", () => {
  inFolders(({ built, target }) => {
    app(target, "partial");
    app(join(dirname(target), ".SecureFolio.app.previous"), "good");
    app(built, "new");
    assert.throws(
      () =>
        replaceApp(
          built,
          target,
          (at) => {
            if (["partial", "new"].includes(versionOf(at))) {
              throw new Error("invalid signature");
            }
          },
          nothingRuns,
        ),
      /invalid signature/,
    );
    assert.equal(versionOf(target), "good");
  });
});

test("with no build, nothing changes", () => {
  inFolders(({ built, target }) => {
    app(target, "old");
    assert.throws(
      () => replaceApp(built, target, passes, nothingRuns),
      /No build/,
    );
    assert.equal(versionOf(target), "old");
  });
});

test("processes are matched by their executable's path, nothing looser", () => {
  const listing = [
    "  101 /Applications/SecureFolio.app/Contents/MacOS/securefolio",
    "  102 /Applications/SecureFolio.app/Contents/Resources/python/bin/python3",
    "  103 /Applications/SecureFolioXapp/Contents/MacOS/securefolio",
    "  104 /Applications/SecureFolio.app.previous/Contents/MacOS/securefolio",
    "  105 /usr/bin/grep",
    "  106 /Volumes/My apps[1]/SecureFolio.app/Contents/MacOS/securefolio",
    "",
  ].join("\n");
  assert.deepEqual(
    pidsIn(listing, "/Applications/SecureFolio.app"),
    [101, 102],
  );
  assert.deepEqual(
    pidsIn(listing, "/Applications/SecureFolio.app", "MacOS/"),
    [101],
  );
  assert.deepEqual(
    pidsIn(listing, "/Volumes/My apps[1]/SecureFolio.app"),
    [106],
  );
});

test("a process list that can't be read stops the install", () => {
  assert.throws(
    () =>
      processesIn("/no/such/place/SecureFolio.app", "", () => {
        throw new Error("Couldn't read the process list");
      }),
    /process list/,
  );
});

test("an app that isn't running has no processes", () => {
  assert.deepEqual(processesIn("/no/such/place/SecureFolio.app"), []);
});

test("the entry check holds for a path with spaces, or one through a link", () => {
  const folder = mkdtempSync(join(tmpdir(), "install app #1 "));
  try {
    const script = join(folder, "install-app.mjs");
    writeFileSync(script, "");
    writeFileSync(join(folder, "other.mjs"), "");
    // tmpdir() is reached through a link on macOS (/var → /private/var); Node
    // gives the module its real path.
    const asNodeRunsIt = pathToFileURL(realpathSync(script)).href;
    assert.equal(isMain(asNodeRunsIt, script), true);
    assert.equal(isMain(asNodeRunsIt, join(folder, "other.mjs")), false);
    assert.equal(isMain(asNodeRunsIt, undefined), false);
  } finally {
    rmSync(folder, { recursive: true });
  }
});
