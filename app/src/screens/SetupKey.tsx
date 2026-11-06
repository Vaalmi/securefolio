import { useRef, useState } from "react";
import type { KeyboardEvent, ReactElement } from "react";
import { CheckCircle2, KeyRound, Loader2, XCircle } from "lucide-react";

import { testProviderKey } from "../api";
import type { CloudProvider } from "../api";
import { go } from "../router";
import { failureOf, LOOK } from "./settings/keyLooks";
import { SetupShell } from "./SetupShell";

// First run, the key step: any one of the three providers, its optional ID, the
// needs-ID state, or no key at all.

const PROVIDERS: {
  id: CloudProvider;
  name: string;
  idLabel: string | null;
}[] = [
  { id: "anthropic", name: "Anthropic", idLabel: "Workspace ID" },
  { id: "openai", name: "OpenAI", idLabel: "Project ID" },
  { id: "openrouter", name: "OpenRouter", idLabel: null },
];

type Status = "idle" | "testing" | "ok" | "bad" | "needs-scope";

function ProviderPicker({
  provider,
  onPick,
}: Readonly<{
  provider: CloudProvider;
  onPick: (index: number) => void;
}>): ReactElement {
  // WAI-ARIA radio group: one tab stop, arrow keys move and select.
  const at = PROVIDERS.findIndex((p) => p.id === provider);
  const onKey = (event: KeyboardEvent<HTMLDivElement>): void => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[
      event.key
    ];
    if (step === undefined) return;
    event.preventDefault();
    onPick((at + step + PROVIDERS.length) % PROVIDERS.length);
  };
  return (
    <div className="field">
      <span id="provider-label">Provider</span>
      <div
        className="seg"
        role="radiogroup"
        tabIndex={-1}
        aria-labelledby="provider-label"
        onKeyDown={onKey}
        style={{ alignSelf: "flex-start" }}
      >
        {PROVIDERS.map((p, i) => (
          <button
            key={p.id}
            id={`provider-${p.id}`}
            type="button"
            role="radio"
            aria-checked={p.id === provider}
            tabIndex={p.id === provider ? 0 : -1}
            onClick={() => {
              onPick(i);
            }}
          >
            {p.name}
          </button>
        ))}
      </div>
    </div>
  );
}

function KeyStatus({
  status,
  message,
}: Readonly<{ status: Status; message: string }>): ReactElement {
  return (
    <span
      id="key-status"
      role="status"
      className="small hstack"
      style={{ gap: 6, minHeight: 20 }}
    >
      {status === "ok" && (
        <>
          <CheckCircle2
            size={14}
            aria-hidden="true"
            style={{ color: "var(--success)" }}
          />{" "}
          Key works. Saved to your Keychain.
        </>
      )}
      {(status === "bad" || status === "needs-scope") && (
        <>
          <XCircle
            size={14}
            aria-hidden="true"
            style={{ color: "var(--danger)" }}
          />{" "}
          {message}
        </>
      )}
      {status === "testing" && "Testing…"}
    </span>
  );
}

function useKeyTest() {
  const [provider, setProvider] = useState<CloudProvider>("anthropic");
  const [key, setKey] = useState("");
  const [scope, setScope] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");
  // A result counts only for the provider, key and ID it tested: each test has a
  // number, and an edit or a newer test makes an older answer stale.
  const round = useRef(0);
  const drop = (): void => {
    round.current += 1;
  };
  const test = async (): Promise<void> => {
    drop();
    const mine = round.current;
    const info = PROVIDERS.find((p) => p.id === provider);
    setStatus("testing");
    try {
      await testProviderKey(provider, {
        key,
        ...(info?.idLabel ? { scope_id: scope } : {}),
      });
      if (mine === round.current) setStatus("ok");
    } catch (failure) {
      if (mine !== round.current) return;
      const found = failureOf(failure);
      setMessage(found.message);
      setStatus(found.needsId ? "needs-scope" : "bad");
    }
  };
  return {
    provider,
    key,
    scope,
    status,
    message,
    test,
    pick: (next: CloudProvider): void => {
      drop();
      setProvider(next);
      setKey("");
      setScope("");
      setStatus("idle");
    },
    typeKey: (next: string): void => {
      drop();
      setKey(next);
      setStatus("idle");
    },
    typeScope: (next: string): void => {
      drop();
      setScope(next);
      // A changed ID needs a new Test; the "needs its ID" message stays until then.
      if (status === "ok" || status === "testing") setStatus("idle");
    },
  };
}

function ScopeField({
  label,
  k,
}: Readonly<{
  label: string;
  k: ReturnType<typeof useKeyTest>;
}>): ReactElement {
  const look = LOOK[k.provider];
  const needs = k.status === "needs-scope";
  return (
    <div className="field">
      <label htmlFor="scope-id">{label} (optional)</label>
      <input
        id="scope-id"
        className="input mono"
        value={k.scope}
        onChange={(event) => {
          k.typeScope(event.target.value);
        }}
        placeholder={look.placeholder}
        aria-describedby={needs ? "key-status scope-help" : "scope-help"}
        aria-invalid={needs && k.scope.trim() === ""}
        autoComplete="off"
        spellCheck={false}
        style={{ maxWidth: 320 }}
      />
      <span id="scope-help" className="help">
        {look.help}
      </span>
    </div>
  );
}

export function KeyStep(): ReactElement {
  const k = useKeyTest();
  const info = PROVIDERS.find((p) => p.id === k.provider) ?? PROVIDERS[0];
  return (
    <SetupShell at={0}>
      <h1>Connect a cloud model</h1>
      <p style={{ color: "var(--ink-2)" }}>
        Optional. A key from any one of these lets Standard documents be
        answered in the cloud; you can add the others later in Settings. Your
        documents are read and indexed on this Mac either way, and Private
        folders never reach any cloud model.
      </p>
      <ProviderPicker
        provider={k.provider}
        onPick={(i) => {
          const next = PROVIDERS[i];
          k.pick(next.id);
          document.getElementById(`provider-${next.id}`)?.focus();
        }}
      />
      <div className="field">
        <label htmlFor="key">{info.name} API key</label>
        <div className="hstack">
          <input
            id="key"
            className="input mono"
            type="password"
            value={k.key}
            onChange={(event) => {
              k.typeKey(event.target.value);
            }}
            placeholder={`${LOOK[k.provider].prefix}…`}
            aria-describedby="key-help key-status"
            aria-invalid={k.status === "bad"}
            autoComplete="off"
          />
          <button
            type="button"
            className="btn"
            disabled={!k.key || k.status === "testing"}
            onClick={() => {
              void k.test();
            }}
          >
            {k.status === "testing" ? (
              <Loader2 size={14} aria-hidden="true" className="spin" />
            ) : (
              <KeyRound size={14} aria-hidden="true" />
            )}
            Test
          </button>
        </div>
        <span id="key-help" className="help">
          Stored in your Mac's Keychain, never in a file. Test sends one tiny
          request to {info.name}.
          {k.provider === "openrouter" &&
            " Your OpenRouter account's settings decide which companies host your requests."}
        </span>
        <KeyStatus status={k.status} message={k.message} />
      </div>
      {info.idLabel && <ScopeField label={info.idLabel} k={k} />}
      <div className="hstack">
        <button
          type="button"
          className="btn ghost"
          onClick={() => {
            go("/setup/models");
          }}
        >
          Continue without a key
        </button>
        <span className="spacer" />
        <button
          type="button"
          className="btn primary"
          disabled={k.status !== "ok"}
          onClick={() => {
            go("/setup/models");
          }}
        >
          Continue
        </button>
      </div>
      <p className="small muted">
        Without a key you can still search, and answers come from the local
        model on this Mac until you add one.
      </p>
    </SetupShell>
  );
}
