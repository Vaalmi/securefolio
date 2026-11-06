import { toast } from "./toast";
import { useEffect, useRef } from "react";
import type { ReactElement } from "react";

import { importPaths } from "./api";
import { ImportModal } from "./components/ImportModal";
import { Palette } from "./components/Palette";
import { ScreenBoundary } from "./components/ScreenBoundary";
import { Tray } from "./components/Tray";
import { onOpenRoute, onPathsDropped, openInMainWindow } from "./desktop";
import { go, useRoute, useRouterBridge, withParam } from "./router";
import type { Route } from "./router";
import { Ask } from "./screens/Ask";
import { Chats } from "./screens/Chats";
import { Collections } from "./screens/Collections";
import { DocumentView } from "./screens/Document";
import { Home } from "./screens/Home";
import { Library } from "./screens/Library";
import { Notes } from "./screens/Notes";
import { QuickSearch } from "./screens/QuickSearch";
import { Settings } from "./screens/Settings";
import { Setup } from "./screens/Setup";
import { Sources } from "./screens/Sources";
import { Sidebar } from "./shell";
import { getSettings } from "./api";
import { ToastHost } from "./ui";

const TITLES: Record<string, string> = {
  home: "Search & Ask",
  library: "Library",
  doc: "Document",
  sources: "Sources",
  collections: "Collections",
  notes: "Notes",
  ask: "Ask",
  chats: "Chats",
  settings: "Settings",
};
const LIST_FILTERS = [
  "type",
  "folder",
  "view",
  "collection",
  "from",
  "to",
  "tag",
] as const;

// With ⌘ and no other modifier. ⇧⌘I is on its own.
function placeFor(route: Route, key: string): string | undefined {
  const places: Record<string, string> = {
    n: "/ask/new",
    f: "/home",
    o: withParam(route, "import", "1"),
    k: withParam(route, "palette", "1"),
    ",": "/settings/models",
  };
  return places[key];
}

function shortcuts(route: Route, event: KeyboardEvent): void {
  if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
  const key = event.key.toLowerCase();
  const place = event.shiftKey
    ? key === "i" && withParam(route, "tray", "all")
    : placeFor(route, key);
  if (place) {
    event.preventDefault();
    go(place);
  }
}

function screenFor(route: Route): ReactElement {
  const first = route.path.at(0);
  const second = route.path.at(1);
  switch (first) {
    case "library":
      return (
        <Library
          key={LIST_FILTERS.map((k) => route.params.get(k) ?? "").join("|")}
        />
      );
    case "collections":
      return second ? (
        <Library key={second} collectionId={second} />
      ) : (
        <Collections />
      );
    case "notes":
      return <Notes />;
    case "doc":
      return (
        <DocumentView
          key={`${second ?? ""}-${route.params.get("el") ?? ""}`}
          id={second ?? ""}
        />
      );
    case "sources":
      return <Sources />;
    case "ask":
      return <Ask key={second ?? "new"} id={second ?? "new"} />;
    case "chats":
      return <Chats />;
    case "settings":
      return <Settings section={second} />;
    default:
      return <Home />;
  }
}

export function App() {
  useRouterBridge();
  const route = useRoute();
  const first = route.path.at(0);

  useEffect(() => {
    const on = (event: KeyboardEvent) => {
      shortcuts(route, event);
    };
    window.addEventListener("keydown", on);
    return () => {
      window.removeEventListener("keydown", on);
    };
  }, [route]);

  // A place chosen in the quick-search window opens in the main one.
  const quickWindow = useRef(first === "quick");
  useEffect(() => {
    if (quickWindow.current && first !== "quick") {
      const place = `/${route.path.join("/")}?${route.params.toString()}`;
      void openInMainWindow(place).then((moved) => {
        if (moved) go("/quick");
      });
    }
  }, [first, route]);

  // First run comes first, until the owner has finished it. Asked on each move, so
  // finishing it takes effect at once.
  useEffect(() => {
    if (first === "setup" || first === "quick") return;
    getSettings()
      .then((settings) => {
        if (!settings.setup_done) go("/setup/key");
      })
      .catch(() => undefined);
  }, [first]);

  useEffect(() => {
    document.title = `${TITLES[first ?? "home"] ?? "SecureFolio"} · SecureFolio`;
  }, [first]);

  // A file dropped anywhere is copied in as the default, and followed in the tray.
  useEffect(() => {
    const stops = [
      onPathsDropped((paths) => {
        importPaths(paths)
          .then(() => {
            go(withParam(route, "tray", "active"));
          })
          .catch((failure: unknown) => {
            toast(failure instanceof Error ? failure.message : String(failure));
          });
      }),
      onOpenRoute(go),
    ];
    return () => {
      for (const stop of stops)
        void stop.then((unlisten) => {
          unlisten();
        });
    };
  }, [route]);

  if (first === "quick") return <QuickSearch />;
  if (first === "setup") {
    return (
      <>
        <Setup key={route.path.at(1)} step={route.path.at(1) ?? "key"} />
        <ToastHost />
      </>
    );
  }
  return (
    <div className="app">
      <a
        href="#main"
        className="sr-only"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        Skip to content
      </a>
      <Sidebar />
      <ScreenBoundary place={`/${route.path.join("/")}?${route.params}`}>
        {screenFor(route)}
        {route.params.has("tray") && <Tray />}
        {route.params.get("import") === "1" && <ImportModal />}
        {route.params.get("palette") === "1" && <Palette />}
      </ScreenBoundary>
      <ToastHost />
    </div>
  );
}
