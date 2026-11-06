import { useRef } from "react";
import type { ReactElement, RefObject } from "react";
import { KeyRound, Trash2 } from "lucide-react";

import { listKeys } from "../../api";
import type { ProviderKey } from "../../api";
import { useLoad } from "../../useLoad";
import { KeyStatus, NewKeyForm, OpenRouterList, ScopeForm } from "./KeyForms";
import { ADDING, LOOK, OPENROUTER_NOTE } from "./keyLooks";
import type { KeyState } from "./keyLooks";
import { useKeyRow } from "./useKeyRow";

// Settings, Keys: a key per provider, with the states keyLooks.ts names.

type Row = ReturnType<typeof useKeyRow>;
type ButtonRef = RefObject<HTMLButtonElement | null>;

function ScopeLine({
  row,
  k,
  button,
}: Readonly<{ row: ProviderKey; k: Row; button: ButtonRef }>): ReactElement {
  return (
    <span
      className="small hstack wrap"
      style={{ gap: 6, color: "var(--ink-2)" }}
    >
      {row.id_label}:{" "}
      {row.scope_id ? (
        <span className="mono">{row.scope_id}</span>
      ) : (
        "none (optional)"
      )}
      <button
        ref={button}
        type="button"
        className="btn sm ghost"
        aria-label={`${row.scope_id ? "Change" : "Add"} the ${row.name} ${row.id_label ?? "ID"}`}
        onClick={k.openScope}
      >
        {row.scope_id ? "Change" : "Add"}
      </button>
    </span>
  );
}

function RemoveConfirm({
  row,
  k,
}: Readonly<{ row: ProviderKey; k: Row }>): ReactElement {
  return (
    <span className="small hstack wrap" style={{ gap: 6, marginTop: 4 }}>
      Remove the {row.name} key? Tasks set to {row.name} models answer on this
      Mac until you add it again or choose another model.
      <button
        type="button"
        className="btn sm danger"
        onClick={() => {
          void k.remove();
        }}
      >
        Remove key
      </button>
      <button
        type="button"
        className="btn sm"
        onClick={() => {
          k.setRemoving(false);
        }}
      >
        Keep it
      </button>
    </span>
  );
}

function KeyActions({
  row,
  k,
  add,
}: Readonly<{
  row: ProviderKey;
  k: Row;
  add: ButtonRef;
}>): ReactElement | null {
  if (!row.present) {
    if (k.state !== "idle") return null;
    return (
      <button
        ref={add}
        type="button"
        className="btn sm"
        onClick={k.startAdding}
      >
        Add key
      </button>
    );
  }
  return (
    <>
      <button
        type="button"
        className="btn sm"
        onClick={() => {
          void k.testSaved();
        }}
      >
        <KeyRound size={12} aria-hidden="true" /> Test
      </button>
      <button
        type="button"
        className="btn sm"
        aria-label={`Remove the ${row.name} key`}
        onClick={() => {
          k.setRemoving(true);
        }}
      >
        <Trash2 size={12} aria-hidden="true" /> Remove
      </button>
    </>
  );
}

// The row's state as shown: the needs-ID warning is the backend's record, never a
// copy kept by the row, so saving an ID or reopening Settings can't leave them apart.
// A test's own refusal also shows it at once: it's the latest word until the
// record reloaded after it comes back, saying the same.
function shownState(
  row: ProviderKey,
  state: KeyState,
  refused: boolean,
): KeyState {
  if (row.needs_id && (state === "idle" || state === "saved-needs-scope"))
    return "saved-needs-scope";
  if (state === "saved-needs-scope") return refused ? state : "idle";
  return state;
}

function needsIdWords(row: ProviderKey): string {
  const label = row.id_label ?? "ID";
  return row.scope_id
    ? `${label} saved. Test the key to check it.`
    : `This key needs its ${label}. Add it, then Test.`;
}

function KeyRow({
  row,
  onChanged,
}: Readonly<{ row: ProviderKey; onChanged: () => void }>): ReactElement {
  const scopeBtn = useRef<HTMLButtonElement>(null);
  const addBtn = useRef<HTMLButtonElement>(null);
  const k = useKeyRow(row, onChanged, scopeBtn, addBtn);
  const shown = shownState(row, k.state, k.message !== "");
  const words =
    shown === "saved-needs-scope" ? k.message || needsIdWords(row) : k.message;
  const hasId = row.id_label !== null && row.present;
  return (
    <div
      className="setting-row"
      style={{ alignItems: "flex-start", flexWrap: "wrap" }}
    >
      <div className="s-text">
        <strong style={{ fontWeight: 500 }}>{row.name}</strong>
        <span className="small" style={{ color: "var(--ink-2)" }}>
          {row.present ? (
            <>
              <span className="mono">
                {LOOK[row.provider].prefix}
                {row.hint}
              </span>{" "}
              · in your Keychain
            </>
          ) : (
            "No key. Optional."
          )}
        </span>
        {hasId && !k.editingScope && (
          <ScopeLine row={row} k={k} button={scopeBtn} />
        )}
        {hasId && k.editingScope && (
          <ScopeForm
            row={row}
            draft={k.scopeDraft}
            setDraft={k.setScopeDraft}
            invalid={shown === "saved-needs-scope" && !k.scopeDraft.trim()}
            onSave={() => {
              void k.saveScope();
            }}
            onCancel={k.closeScope}
          />
        )}
        {row.provider === "openrouter" && (
          <>
            <span className="small" style={{ color: "var(--ink-2)" }}>
              {OPENROUTER_NOTE}
            </span>
            {row.present && <OpenRouterList onRefreshed={onChanged} />}
          </>
        )}
        {ADDING.includes(k.state) && (
          <NewKeyForm
            row={row}
            look={LOOK[row.provider]}
            value={k.value}
            setValue={k.typeKey}
            scope={k.newScope}
            setScope={k.typeScope}
            state={k.state}
            onTest={() => {
              void k.testNew();
            }}
            onCancel={k.cancelAdding}
          />
        )}
        <KeyStatus id={`key-${row.provider}`} state={shown} message={words} />
        {k.removing && <RemoveConfirm row={row} k={k} />}
        {k.problem && (
          <span role="alert" className="small">
            {k.problem}
          </span>
        )}
      </div>
      <span className="hstack" style={{ gap: 6 }}>
        <KeyActions row={row} k={k} add={addBtn} />
      </span>
    </div>
  );
}

// onChanged: a key came, went or changed, so whatever follows the keys reloads too.
export function KeysCard({
  onChanged,
}: Readonly<{ onChanged?: () => void }>): ReactElement {
  const keys = useLoad(listKeys, "keys");
  const reload = (): void => {
    keys.reload();
    onChanged?.();
  };
  return (
    <div className="card">
      <h2 style={{ marginBottom: 4 }}>Keys</h2>
      <p className="small" style={{ color: "var(--ink-2)", marginBottom: 8 }}>
        Each key is optional and kept only in your Keychain; only its last four
        characters are shown. With no key at all, search works and answers come
        from the local model.
      </p>
      {keys.data?.map((row) => (
        <KeyRow key={row.provider} row={row} onChanged={reload} />
      ))}
      {keys.error && (
        <p role="alert" className="small">
          {keys.error}
        </p>
      )}
    </div>
  );
}
