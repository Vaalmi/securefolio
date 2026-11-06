import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent, ReactElement } from "react";
import { ChevronDown, Library, Lock } from "lucide-react";

import type { AskRoute, Mode } from "../api";
import { useAskRoute } from "../askRoute";

export interface ScopeOption {
  id: string;
  label: string;
  desc?: string;
  private?: boolean;
  collection?: string;
}

export function ScopeChip({
  value,
  options,
  onChange,
  locked,
  placement = "up",
}: Readonly<{
  value: ScopeOption;
  options: ScopeOption[];
  onChange: (o: ScopeOption) => void;
  locked?: boolean;
  placement?: "up" | "down";
}>): ReactElement {
  const [open, setOpen] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (open)
      listRef.current
        ?.querySelector<HTMLElement>(
          "[aria-checked=true], [role=menuitemradio]",
        )
        ?.focus();
  }, [open]);
  const onKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    const its = Array.from(
      listRef.current?.querySelectorAll<HTMLElement>("[role=menuitemradio]") ??
        [],
    );
    const i = its.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      its[(i + 1) % its.length]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      its[(i - 1 + its.length) % its.length]?.focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
      btnRef.current?.focus();
    }
  };
  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button
        ref={btnRef}
        type="button"
        className="chip scope-chip"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Scope: ${value.label}${locked ? ". Locked: this chat is Private" : ". Change scope"}`}
        onClick={() => {
          if (!locked) setOpen((o) => !o);
        }}
        aria-disabled={locked}
      >
        {value.private ? (
          <Lock size={14} aria-hidden="true" />
        ) : (
          <Library size={14} aria-hidden="true" />
        )}
        <span className="lbl">{value.label}</span>
        {!locked && <ChevronDown size={14} aria-hidden="true" />}
      </button>
      {open && (
        <div
          ref={listRef}
          role="menu"
          tabIndex={-1}
          aria-label="Scope"
          className="popover"
          style={
            placement === "up"
              ? { bottom: "100%", left: 0, marginBottom: 6, width: 320 }
              : { top: "100%", left: 0, marginTop: 6, width: 320 }
          }
          onKeyDown={onKey}
          onBlur={(e) => {
            if (!listRef.current?.contains(e.relatedTarget)) setOpen(false);
          }}
        >
          {options.map((o) => (
            <button
              key={o.id}
              type="button"
              role="menuitemradio"
              aria-checked={o.id === value.id}
              tabIndex={-1}
              className="menu-item"
              onClick={() => {
                onChange(o);
                setOpen(false);
                btnRef.current?.focus();
              }}
              style={{ alignItems: "flex-start" }}
            >
              {o.private ? (
                <Lock size={14} aria-hidden="true" style={{ marginTop: 3 }} />
              ) : (
                <Library
                  size={14}
                  aria-hidden="true"
                  style={{ marginTop: 3 }}
                />
              )}
              <span style={{ display: "flex", flexDirection: "column" }}>
                <span>{o.label}</span>
                {o.desc && <span className="desc">{o.desc}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

/** "Cloud AI, <model>" or "Local AI on this Mac, <tag>", for the modes' tooltips. */
function whoHint(route: AskRoute | undefined): string {
  if (!route) return "";
  return route.local
    ? `Local AI on this Mac, ${route.name}`
    : `Cloud AI, ${route.name}`;
}

export function ModeSwitch({
  value,
  onChange,
  lockedPrivate,
}: Readonly<{
  value: Mode;
  onChange: (m: Mode) => void;
  lockedPrivate?: boolean;
}>): ReactElement {
  const standard = useAskRoute({ mode: "standard" }).data;
  const deep = useAskRoute({ mode: "deep" }).data;
  const local = useAskRoute({ mode: "private" }).data;
  const modes: { m: Mode; label: string; hint: string }[] = [
    { m: "standard", label: "Standard", hint: whoHint(standard) },
    {
      m: "deep",
      label: "Deep",
      hint: deep?.local
        ? `${whoHint(deep)}; Deep needs a cloud key`
        : `${whoHint(deep)}, slower and costs more`,
    },
    { m: "private", label: "Private", hint: whoHint(local) },
  ];
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    const i = modes.findIndex((x) => x.m === value);
    let n = -1;
    if (e.key === "ArrowRight") n = (i + 1) % 3;
    if (e.key === "ArrowLeft") n = (i + 2) % 3;
    if (n >= 0) {
      e.preventDefault();
      if (lockedPrivate) return;
      onChange(modes[n].m);
      refs.current[n]?.focus();
    }
  };
  return (
    <div
      className="seg"
      role="radiogroup"
      tabIndex={-1}
      aria-label="Answer mode"
      onKeyDown={onKey}
    >
      {modes.map((x, i) => (
        <button
          key={x.m}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="radio"
          aria-checked={x.m === value}
          tabIndex={x.m === value ? 0 : -1}
          title={x.hint}
          aria-disabled={lockedPrivate && x.m !== "private"}
          onClick={() => {
            if (!lockedPrivate) onChange(x.m);
          }}
        >
          {x.m === "private" && <Lock size={12} aria-hidden="true" />}
          {x.label}
        </button>
      ))}
    </div>
  );
}
