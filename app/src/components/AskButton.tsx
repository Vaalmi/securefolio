import type { ReactElement } from "react";
import { AlertTriangle, Cloud, Cpu } from "lucide-react";

import type { AskRoute, Mode } from "../api";
import { askLabel, canAsk } from "../askRoute";

// The Ask button and the note beside it, for every Ask box, from the backend's route.

/** The Ask button. Its label is the backend's route answer, not worked out here, and
 * a route nothing can answer disables it. Return is its key, unless Return opens a
 * result the arrows moved to; then, or while it's blocked, it shows no key. */
export function AskButton({
  route,
  mode,
  disabled,
  onClick,
  returnOpens = false,
}: Readonly<{
  route: AskRoute | undefined;
  mode: Mode;
  disabled: boolean;
  onClick: () => void;
  returnOpens?: boolean;
}>): ReactElement {
  const ready = canAsk(route);
  const showsKey = ready && !returnOpens;
  return (
    <button
      type="button"
      className="btn primary"
      disabled={disabled || !ready}
      onClick={onClick}
      aria-keyshortcuts={showsKey ? "Enter" : undefined}
      title={route?.name}
    >
      {route?.local && <Cpu size={14} aria-hidden="true" />}
      {route && !route.local && <Cloud size={14} aria-hidden="true" />}
      {askLabel(route, mode)}
      {showsKey && (
        <span className="kbd" aria-hidden="true">
          ↵
        </span>
      )}
    </button>
  );
}

/** Why a question can't be answered now, or why Local AI answers it, with the way to
 * change that. Nothing when Cloud AI answers. */
export function RouteNote({
  route,
  error,
}: Readonly<{
  route: AskRoute | undefined;
  error?: string;
}>): ReactElement | null {
  if (!route) {
    if (!error) return null; // still finding out
    return (
      <span className="small hstack needs-key" style={{ gap: 4 }}>
        <AlertTriangle size={12} aria-hidden="true" />
        SecureFolio couldn't tell who would answer, so it can't ask yet. {error}
      </span>
    );
  }
  if (route.blocked) {
    return (
      <span className="small hstack needs-key" style={{ gap: 4 }}>
        <AlertTriangle size={12} aria-hidden="true" />
        {route.blocked}{" "}
        {route.local ? (
          <a href="#/settings/local">Local models</a>
        ) : (
          <a href="#/settings/models">Keys and models</a>
        )}
      </span>
    );
  }
  if (!route.local) return null;
  if (route.needs_key) {
    return (
      <span className="small hstack needs-key" style={{ gap: 4 }}>
        <AlertTriangle size={12} aria-hidden="true" />
        Local AI answers on this Mac. {route.reason}{" "}
        <a href="#/settings/models">Keys and models</a>
      </span>
    );
  }
  return (
    <span className="small muted hstack" style={{ gap: 4 }}>
      <Cpu size={12} aria-hidden="true" />
      {route.reason === "Offline"
        ? "Offline: Local AI answers on this Mac"
        : "Local AI answers on this Mac"}
    </span>
  );
}
