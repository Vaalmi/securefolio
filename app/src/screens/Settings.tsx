import { useState } from "react";
import type { ReactElement } from "react";
import { Cpu, Lock, Pencil, Pin, PinOff, Trash2 } from "lucide-react";

import {
  deleteMemory,
  editMemory,
  getMemorySwitch,
  listMemories,
  setMemorySwitch,
} from "../api";
import type { Memory } from "../api";
import { fmtMoment } from "../format";
import { Page } from "../shell";
import { toast } from "../toast";
import { Modal, PrivateBadge, Switch } from "../ui";
import { useAction } from "../useAction";
import { useLoad } from "../useLoad";
import { Backup } from "./settings/Backup";
import { Local } from "./settings/Local";
import { Models } from "./settings/Models";
import { Privacy } from "./settings/Privacy";
import { Spending } from "./settings/Spending";

function MemoryRow({
  memory,
  onChanged,
  onDelete,
}: Readonly<{
  memory: Memory;
  onChanged: () => void;
  onDelete: () => void;
}>): ReactElement {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(memory.text);
  const save = useAction(
    () => editMemory(memory.id, { text }),
    () => {
      setEditing(false);
      onChanged();
    },
  );
  const pin = useAction(
    () => editMemory(memory.id, { pinned: !memory.pinned }),
    onChanged,
  );
  return (
    <div className="setting-row" style={{ alignItems: "flex-start" }}>
      <span className="badge outline" style={{ marginTop: 2 }}>
        {memory.kind}
      </span>
      <div className="s-text">
        {editing ? (
          <form
            className="stack"
            style={{ gap: 6 }}
            onSubmit={(event) => {
              event.preventDefault();
              void save.run();
            }}
          >
            <label htmlFor={`mem-${memory.id}`} className="sr-only">
              Edit memory
            </label>
            <textarea
              id={`mem-${memory.id}`}
              className="textarea"
              rows={2}
              value={text}
              onChange={(event) => {
                setText(event.target.value);
              }}
            />
            {save.error && (
              <span role="alert" className="small">
                {save.error}
              </span>
            )}
            <span className="hstack">
              <button type="submit" className="btn primary sm">
                Save
              </button>
              <button
                type="button"
                className="btn sm"
                onClick={() => {
                  setEditing(false);
                }}
              >
                Cancel
              </button>
            </span>
          </form>
        ) : (
          <span>{memory.text}</span>
        )}
        <span className="small muted hstack wrap" style={{ gap: 6 }}>
          {memory.source_label} · {fmtMoment(memory.created_at)}
          {memory.pinned && (
            <span className="hstack" style={{ gap: 2 }}>
              · <Pin size={11} aria-hidden="true" /> Pinned
            </span>
          )}
          {memory.expires_at !== null && (
            <span>· Expires {fmtMoment(memory.expires_at)}</span>
          )}
          {memory.sensitivity === "private" && <PrivateBadge />}
        </span>
      </div>
      <span className="hstack" style={{ gap: 2 }}>
        {memory.kind === "recap" && (
          <button
            type="button"
            className="icon-btn"
            aria-pressed={memory.pinned}
            aria-label={`${memory.pinned ? "Unpin" : "Pin"} recap: ${memory.text}`}
            title={
              memory.pinned
                ? "Unpin: expires 180 days after the chat"
                : "Pin: keep past 180 days"
            }
            onClick={() => {
              void pin.run();
            }}
          >
            {memory.pinned ? (
              <PinOff size={14} aria-hidden="true" />
            ) : (
              <Pin size={14} aria-hidden="true" />
            )}
          </button>
        )}
        <button
          type="button"
          className="icon-btn"
          aria-label={`Edit memory: ${memory.text}`}
          onClick={() => {
            setEditing(true);
          }}
        >
          <Pencil size={14} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="icon-btn"
          aria-label={`Delete memory: ${memory.text}`}
          onClick={onDelete}
        >
          <Trash2 size={14} aria-hidden="true" />
        </button>
      </span>
    </div>
  );
}

function MemoryPage(): ReactElement {
  const memories = useLoad(listMemories, "memories");
  const memorySwitch = useLoad(getMemorySwitch, "switch");
  const [confirm, setConfirm] = useState<Memory | null>(null);
  const flip = useAction(setMemorySwitch, memorySwitch.reload);
  const remove = useAction(
    (id: string) => deleteMemory(id),
    () => {
      setConfirm(null);
      toast("Memory deleted. It won't come back.");
      memories.reload();
    },
  );
  return (
    <>
      <div className="card">
        <div className="setting-row">
          <div className="s-text">
            <strong style={{ fontWeight: 500 }}>Memory</strong>
            <span className="small" style={{ color: "var(--ink-2)" }}>
              SecureFolio remembers preferences, facts you state about yourself
              and a one-line recap of each finished chat. It never stores
              document content. Recaps expire after 180 days unless pinned.
            </span>
          </div>
          <Switch
            hideLabel
            label="Memory"
            checked={memorySwitch.data?.on ?? true}
            onChange={(on) => {
              void flip.run(on);
            }}
          />
        </div>
        <p className="small" style={{ color: "var(--ink-2)" }}>
          To turn memory off for one chat, use the Memory on button in that
          chat's header.
        </p>
      </div>
      <div className="card">
        <h2 style={{ marginBottom: 8 }}>
          What SecureFolio remembers{" "}
          <span className="small muted mono">{memories.data?.length ?? 0}</span>
        </h2>
        {memories.error && <p role="alert">{memories.error}</p>}
        {memories.data?.map((memory) => (
          <MemoryRow
            key={memory.id}
            memory={memory}
            onChanged={memories.reload}
            onDelete={() => {
              setConfirm(memory);
            }}
          />
        ))}
      </div>
      {confirm && (
        <Modal
          title="Delete this memory?"
          onClose={() => {
            setConfirm(null);
          }}
          footer={
            <>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setConfirm(null);
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn danger"
                onClick={() => {
                  void remove.run(confirm.id);
                }}
              >
                Delete memory
              </button>
            </>
          }
        >
          <div className="modal-body">
            <p>“{confirm.text}”</p>
            <p className="small" style={{ color: "var(--ink-2)" }}>
              It won't be learned again, it's removed from any chat summary that
              quoted it, and it drops out of any question being prepared now.
            </p>
            {remove.error && (
              <p role="alert" className="small">
                {remove.error}
              </p>
            )}
          </div>
        </Modal>
      )}
    </>
  );
}

// Settings' sections, in the order the nav lists them; the first is the default.
const SECTIONS = [
  { id: "models", label: "Keys and models", body: <Models /> },
  { id: "local", label: "Local models and Offline", body: <Local /> },
  { id: "privacy", label: "Privacy and MCP", body: <Privacy /> },
  { id: "spend", label: "Spending and cloud log", body: <Spending /> },
  { id: "memory", label: "Memory", body: <MemoryPage /> },
  { id: "backup", label: "Backup", body: <Backup /> },
];

export function Settings({
  section,
}: Readonly<{ section?: string }>): ReactElement {
  const shown = SECTIONS.find((s) => s.id === section) ?? SECTIONS[0];
  return (
    <Page title="Settings">
      <div className="settings">
        <nav className="settings-nav" aria-label="Settings sections">
          {SECTIONS.map((s) => (
            <a
              key={s.id}
              href={`#/settings/${s.id}`}
              aria-current={s.id === shown.id ? "page" : undefined}
            >
              {s.label}
            </a>
          ))}
        </nav>
        <div className="stack" aria-labelledby="settings-h">
          <h2 id="settings-h" className="hstack" style={{ gap: 6 }}>
            {shown.id === "local" && <Cpu size={16} aria-hidden="true" />}
            {shown.id === "privacy" && <Lock size={16} aria-hidden="true" />}
            {shown.label}
          </h2>
          {shown.body}
        </div>
      </div>
    </Page>
  );
}
