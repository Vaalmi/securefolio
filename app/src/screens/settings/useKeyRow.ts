import { useRef, useState } from "react";
import type { RefObject } from "react";

import { removeKey, setKeyScope, testProviderKey } from "../../api";
import type { ProviderKey } from "../../api";
import { toast } from "../../toast";
import { failureOf, focusLater } from "./keyLooks";
import type { KeyState } from "./keyLooks";

// One provider's key row: its state and what each button does. The needs-ID state
// comes from the backend, so it's kept when Settings is left and reopened; each form
// gives focus back to the button that opened it.

// The two refs are the row's own: the buttons focus goes back to.
export function useKeyRow(
  row: ProviderKey,
  onChanged: () => void,
  scopeBtn: RefObject<HTMLButtonElement | null>,
  addBtn: RefObject<HTMLButtonElement | null>,
) {
  const [state, setState] = useState<KeyState>(
    row.needs_id ? "saved-needs-scope" : "idle",
  );
  // A test's own reason, while it's the latest word; otherwise the row's warning
  // follows the backend's record (see shownState in Keys.tsx).
  const [message, setMessage] = useState("");
  const [value, setValue] = useState("");
  const [newScope, setNewScope] = useState("");
  const [editingScope, setEditingScope] = useState(row.needs_id);
  const [scopeDraft, setScopeDraft] = useState(row.scope_id ?? "");
  const [removing, setRemoving] = useState(false);
  // Why saving an ID or removing the key didn't go, shown where it was asked.
  const [problem, setProblem] = useState("");
  // A test's answer counts only for the key and ID it tested, as in SetupKey: each
  // test has a number, and an edit, a Cancel or a newer test makes an older answer
  // stale. A stale answer still reloads the row, as the backend acted on it.
  const round = useRef(0);
  const drop = (): void => {
    round.current += 1;
  };

  // A needs-ID refusal is about the ID in the open form: once the form closes, by
  // Save, Cancel or a passing test, the row's warning is the backend's record again.
  const closeScope = (): void => {
    if (state === "saved-needs-scope") {
      setState("idle");
      setMessage("");
    }
    setEditingScope(false);
    focusLater(scopeBtn);
  };
  const failed = (failure: unknown, saved: boolean): void => {
    const found = failureOf(failure);
    setMessage(found.message);
    if (found.needsId && saved) {
      setState("saved-needs-scope");
      setEditingScope(true);
      onChanged(); // the backend marked the saved key: its record says so now
    } else setState(found.needsId ? "needs-scope" : "bad");
  };
  const testNew = async (): Promise<void> => {
    drop();
    const mine = round.current;
    setState("testing");
    try {
      await testProviderKey(row.provider, {
        key: value,
        ...(row.id_label ? { scope_id: newScope } : {}),
      });
      onChanged(); // saved: the row shows the key that worked
      if (mine !== round.current) return; // what's typed now hasn't been tested
      setValue("");
      setState("ok");
    } catch (failure) {
      if (mine === round.current) failed(failure, false);
    }
  };
  const testSaved = async (): Promise<void> => {
    drop();
    const mine = round.current;
    const id = editingScope ? scopeDraft.trim() : (row.scope_id ?? "");
    try {
      await testProviderKey(row.provider, editingScope ? { scope_id: id } : {});
      onChanged();
      if (mine !== round.current) return;
      if (editingScope) closeScope();
      setState("idle");
      toast(`${row.name} key works.`);
    } catch (failure) {
      if (mine === round.current) failed(failure, true);
      else onChanged(); // a needs-ID refusal marks the saved key all the same
    }
  };
  const saveScope = async (): Promise<void> => {
    drop();
    const mine = round.current;
    const draft = scopeDraft.trim();
    setProblem("");
    try {
      await setKeyScope(row.provider, draft);
    } catch (failure) {
      // The form stays open, with what was typed; a reason for an older draft isn't
      // shown against a newer one.
      if (mine === round.current) setProblem(failureOf(failure).message);
      return;
    }
    if (mine !== round.current) {
      onChanged(); // saved: the row shows it, and the newer draft stays to be saved
      return;
    }
    // Whether the warning stays is the backend's to say, in the record that comes
    // back; a test's old reason no longer applies.
    setMessage("");
    closeScope();
    const what = `${row.name} ${row.id_label ?? "ID"}`;
    toast(draft ? `${what} saved.` : `${what} removed.`);
    onChanged();
  };
  const remove = async (): Promise<void> => {
    setProblem("");
    try {
      await removeKey(row.provider);
    } catch (failure) {
      setProblem(failureOf(failure).message);
      return; // the confirmation stays, and the key is still there
    }
    drop(); // a test of the removed key answers about a key that's gone
    setRemoving(false);
    setState("idle");
    setValue("");
    setNewScope("");
    setEditingScope(false);
    toast(`${row.name} key removed from your Keychain.`);
    onChanged();
    focusLater(addBtn);
  };
  const startAdding = (): void => {
    setState("adding");
  };
  const cancelAdding = (): void => {
    drop();
    setState("idle");
    focusLater(addBtn);
  };
  const openScope = (): void => {
    drop();
    setScopeDraft(row.scope_id ?? "");
    setEditingScope(true);
  };
  const typeKey = (next: string): void => {
    drop();
    setValue(next);
    setState("adding");
  };
  const typeScope = (next: string): void => {
    drop();
    setNewScope(next);
    if (state === "testing") setState("adding");
  };
  const typeScopeDraft = (next: string): void => {
    drop();
    setScopeDraft(next);
  };
  return {
    state,
    message,
    value,
    newScope,
    editingScope,
    scopeDraft,
    removing,
    problem,
    setScopeDraft: typeScopeDraft,
    setRemoving,
    closeScope: (): void => {
      drop();
      closeScope();
    },
    testNew,
    testSaved,
    saveScope,
    remove,
    startAdding,
    cancelAdding,
    openScope,
    typeKey,
    typeScope,
  };
}
