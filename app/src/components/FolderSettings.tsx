import { useRef } from "react";
import type { KeyboardEvent, ReactElement } from "react";
import { Lock } from "lucide-react";

import type { Sensitivity } from "../api";
import { PrivateBadge, Switch } from "../ui";

export function SensitivityPicker({
  value,
  onChange,
  name,
}: Readonly<{
  value: Sensitivity;
  onChange: (v: Sensitivity) => void;
  name: string;
}>): ReactElement {
  // WAI-ARIA radio group: one tab stop, arrow keys move and select.
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const opts: Sensitivity[] = ["standard", "private"];
  const onKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    if (!["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"].includes(e.key))
      return;
    e.preventDefault();
    const next = value === "standard" ? "private" : "standard";
    onChange(next);
    refs.current[opts.indexOf(next)]?.focus();
  };
  return (
    <div
      className="radio-cards"
      role="radiogroup"
      tabIndex={-1}
      aria-label={`Sensitivity for ${name}`}
      onKeyDown={onKey}
    >
      <button
        ref={(el) => {
          refs.current[0] = el;
        }}
        type="button"
        role="radio"
        aria-checked={value === "standard"}
        tabIndex={value === "standard" ? 0 : -1}
        className="radio-card"
        onClick={() => {
          onChange("standard");
        }}
      >
        <span className="radio-dot" aria-hidden="true" />
        <span>
          <strong>Standard</strong>
          <span
            className="small"
            style={{ display: "block", color: "var(--ink-2)" }}
          >
            Answers can use the cloud model you chose, with only the passages
            they need.
          </span>
        </span>
      </button>
      <button
        ref={(el) => {
          refs.current[1] = el;
        }}
        type="button"
        role="radio"
        aria-checked={value === "private"}
        tabIndex={value === "private" ? 0 : -1}
        className="radio-card"
        onClick={() => {
          onChange("private");
        }}
      >
        <span className="radio-dot" aria-hidden="true" />
        <span>
          <strong className="hstack" style={{ gap: 4 }}>
            <Lock size={12} aria-hidden="true" /> Private
          </strong>
          <span
            className="small"
            style={{ display: "block", color: "var(--ink-2)" }}
          >
            Never sent to any cloud model or MCP. Answers come from the local
            model.
          </span>
        </span>
      </button>
    </div>
  );
}

export interface FolderDraft {
  path: string;
  sensitivity: Sensitivity;
  organize: boolean;
  mcp: boolean;
}

export function FolderSettings({
  f,
  onChange,
}: Readonly<{
  f: FolderDraft;
  onChange: (f: FolderDraft) => void;
}>): ReactElement {
  const isPrivate = f.sensitivity === "private";
  const id = f.path.replace(/[^a-z]/gi, "");
  return (
    <div className="folder-row">
      <div className="hstack">
        <strong className="mono" style={{ flex: 1 }}>
          {f.path}
        </strong>
        {isPrivate && <PrivateBadge />}
      </div>
      <SensitivityPicker
        value={f.sensitivity}
        name={f.path}
        onChange={(v) => {
          onChange({
            ...f,
            sensitivity: v,
            organize: v === "private" ? false : f.organize,
            mcp: v === "standard",
          });
        }}
      />
      {isPrivate ? (
        <p className="small hstack" style={{ gap: 6, color: "var(--ink-2)" }}>
          <Lock size={12} aria-hidden="true" /> Cloud organizing and MCP access
          aren't offered for Private folders. Cards come from the local model.
        </p>
      ) : (
        <div className="stack" style={{ gap: 4 }}>
          <Switch
            checked={f.mcp}
            onChange={(v) => {
              onChange({ ...f, mcp: v });
            }}
            label="Claude Desktop and Claude Code can search this folder"
            describedBy={`${id}-mcp`}
          />
          <span id={`${id}-mcp`} className="help" style={{ paddingLeft: 42 }}>
            Read-only, through SecureFolio's MCP server.
          </span>
        </div>
      )}
    </div>
  );
}
