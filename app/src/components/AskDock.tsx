import { useEffect, useId, useRef, useState } from "react";
import type { ReactElement } from "react";
import { Square } from "lucide-react";

import type { AskRoute } from "../api";
import { canAsk } from "../askRoute";
import { isSendKey } from "../keys";
import { useApp } from "../storeContext";
import { PrivateBadge } from "../ui";
import { AskButton, RouteNote } from "./AskButton";
import { ModeSwitch, ScopeChip } from "./ScopeChip";
import type { ScopeOption } from "./ScopeChip";

export function AskDock({
  scope,
  scopeOptions,
  onScope,
  threadPrivate,
  route,
  routeError,
  running,
  onSend,
  onStop,
  placeholder,
  focusOnOpen,
}: Readonly<{
  scope: ScopeOption;
  scopeOptions: ScopeOption[];
  onScope: (option: ScopeOption) => void;
  threadPrivate: boolean;
  route: AskRoute | undefined; // who answers this thread's next question
  routeError: string | undefined;
  running: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  placeholder: string;
  focusOnOpen: boolean;
}>): ReactElement {
  const app = useApp();
  const [text, setText] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const id = useId();
  useEffect(() => {
    if (focusOnOpen) box.current?.focus();
  }, [focusOnOpen]);
  const send = (): void => {
    if (!text.trim() || running || !canAsk(route)) return;
    onSend(text.trim());
    setText("");
  };
  return (
    <div className="composer">
      <label htmlFor={id} className="sr-only">
        {placeholder}
      </label>
      <textarea
        ref={box}
        id={id}
        rows={2}
        value={text}
        placeholder={placeholder}
        onChange={(event) => {
          setText(event.target.value);
        }}
        onKeyDown={(event) => {
          if (isSendKey(event)) {
            event.preventDefault();
            send();
          }
        }}
        aria-describedby={`${id}-hint`}
      />
      <div className="composer-bar">
        <ScopeChip
          value={scope}
          options={scopeOptions}
          onChange={onScope}
          locked={threadPrivate}
        />
        <ModeSwitch
          value={threadPrivate ? "private" : app.mode}
          onChange={app.setMode}
          lockedPrivate={threadPrivate}
        />
        {threadPrivate && <PrivateBadge label="Private thread" />}
        <RouteNote route={route} error={routeError} />
        <span className="spacer" />
        <span id={`${id}-hint`} className="small muted">
          <span className="kbd">↵</span> send · <span className="kbd">⇧↵</span>{" "}
          new line
        </span>
        {running ? (
          <button type="button" className="btn" onClick={onStop}>
            <Square size={12} aria-hidden="true" /> Stop
          </button>
        ) : (
          <AskButton
            route={route}
            mode={app.mode}
            disabled={!text.trim()}
            onClick={send}
          />
        )}
      </div>
    </div>
  );
}
