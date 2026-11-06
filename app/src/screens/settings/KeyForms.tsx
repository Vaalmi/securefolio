import { useEffect, useRef } from "react";
import type { ReactElement } from "react";
import {
  CheckCircle2,
  KeyRound,
  Loader2,
  RefreshCw,
  XCircle,
} from "lucide-react";

import { getOpenrouterList, refreshOpenrouterList } from "../../api";
import type { ProviderKey } from "../../api";
import { useAction } from "../../useAction";
import { useLoad } from "../../useLoad";
import { LOOK } from "./keyLooks";
import type { KeyState } from "./keyLooks";

// The Keys card's forms and lines.

// A form opens on the owner's click, with its field focused.
function useFocusOnOpen() {
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    field.current?.focus();
  }, []);
  return field;
}

export function KeyStatus({
  id,
  state,
  message,
}: Readonly<{ id: string; state: KeyState; message: string }>): ReactElement {
  return (
    <span
      id={`${id}-status`}
      role="status"
      className="small hstack"
      style={{ gap: 6 }}
    >
      {state === "ok" && (
        <>
          <CheckCircle2
            size={14}
            aria-hidden="true"
            style={{ color: "var(--success)" }}
          />{" "}
          Key works. Saved to your Keychain.
        </>
      )}
      {(state === "bad" ||
        state === "needs-scope" ||
        state === "saved-needs-scope") && (
        <>
          <XCircle
            size={14}
            aria-hidden="true"
            style={{ color: "var(--danger)" }}
          />{" "}
          {message}
        </>
      )}
    </span>
  );
}

interface ScopeFormProps {
  row: ProviderKey;
  draft: string;
  setDraft: (value: string) => void;
  invalid: boolean;
  onSave: () => void;
  onCancel: () => void;
}

export function ScopeForm({
  row,
  draft,
  setDraft,
  invalid,
  onSave,
  onCancel,
}: Readonly<ScopeFormProps>): ReactElement {
  const look = LOOK[row.provider];
  const id = `scope-${row.provider}`;
  const field = useFocusOnOpen();
  return (
    <form
      className="stack"
      style={{ gap: 4, marginTop: 4 }}
      onSubmit={(event) => {
        event.preventDefault();
        onSave();
      }}
    >
      <label htmlFor={id} className="small">
        {row.id_label} (optional)
      </label>
      <span className="hstack">
        <input
          ref={field}
          id={id}
          className="input mono"
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
          }}
          placeholder={look.placeholder}
          aria-invalid={invalid}
          aria-describedby={
            invalid ? `key-${row.provider}-status ${id}-help` : `${id}-help`
          }
          autoComplete="off"
          spellCheck={false}
          style={{ width: 220 }}
        />
        <button type="submit" className="btn sm">
          Save
        </button>
        <button type="button" className="btn sm ghost" onClick={onCancel}>
          Cancel
        </button>
      </span>
      <span id={`${id}-help`} className="help">
        {look.help}
      </span>
    </form>
  );
}

// onRefreshed: a fresh list can add models, or drop a chosen one, so the task
// lists reload with it.
export function OpenRouterList({
  onRefreshed,
}: Readonly<{ onRefreshed: () => void }>): ReactElement {
  const list = useLoad(getOpenrouterList, "openrouter-list");
  const refresh = useAction(refreshOpenrouterList, () => {
    list.reload();
    onRefreshed();
  });
  const at = list.data?.fetched_at;
  return (
    <span
      className="small hstack wrap"
      style={{ gap: 6, color: "var(--ink-2)" }}
    >
      {at
        ? `Its model list is from ${new Date(at * 1000).toLocaleDateString()}.`
        : "Its model list hasn't been fetched yet."}
      <button
        type="button"
        className="btn sm ghost"
        disabled={refresh.busy}
        onClick={() => {
          void refresh.run();
        }}
      >
        <RefreshCw size={12} aria-hidden="true" /> Refresh
      </button>
      {(refresh.error ?? list.data?.failed) && (
        <span role="alert">{refresh.error ?? list.data?.failed}</span>
      )}
    </span>
  );
}

interface NewKeyFormProps {
  row: ProviderKey;
  look: (typeof LOOK)[ProviderKey["provider"]];
  value: string;
  setValue: (value: string) => void;
  scope: string;
  setScope: (value: string) => void;
  state: KeyState;
  onTest: () => void;
  onCancel: () => void;
}

export function NewKeyForm({
  row,
  look,
  value,
  setValue,
  scope,
  setScope,
  state,
  onTest,
  onCancel,
}: Readonly<NewKeyFormProps>): ReactElement {
  const inputId = `key-${row.provider}`;
  const field = useFocusOnOpen();
  return (
    <>
      <span className="hstack" style={{ marginTop: 6 }}>
        <label htmlFor={inputId} className="sr-only">
          {row.name} API key
        </label>
        <input
          ref={field}
          id={inputId}
          className="input mono"
          type="password"
          autoComplete="off"
          placeholder={`${look.prefix}…`}
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
          }}
          aria-invalid={state === "bad"}
          aria-describedby={`${inputId}-status`}
          style={{ width: 260 }}
        />
      </span>
      {row.id_label && (
        <span className="stack" style={{ gap: 4, marginTop: 4 }}>
          <label htmlFor={`${inputId}-scope`} className="small">
            {row.id_label} (optional)
          </label>
          <input
            id={`${inputId}-scope`}
            className="input mono"
            value={scope}
            onChange={(event) => {
              setScope(event.target.value);
            }}
            placeholder={look.placeholder}
            aria-describedby={
              state === "needs-scope"
                ? `${inputId}-status ${inputId}-scope-help`
                : `${inputId}-scope-help`
            }
            aria-invalid={state === "needs-scope" && !scope.trim()}
            autoComplete="off"
            spellCheck={false}
            style={{ width: 260 }}
          />
          <span id={`${inputId}-scope-help`} className="help">
            {look.help}
          </span>
        </span>
      )}
      <span className="hstack" style={{ marginTop: 6 }}>
        <button
          type="button"
          className="btn sm"
          disabled={!value || state === "testing"}
          onClick={onTest}
        >
          {state === "testing" ? (
            <Loader2 size={12} aria-hidden="true" className="spin" />
          ) : (
            <KeyRound size={12} aria-hidden="true" />
          )}{" "}
          Test and save
        </button>
        <button type="button" className="btn sm ghost" onClick={onCancel}>
          Cancel
        </button>
      </span>
    </>
  );
}
