import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
  type KeyboardEvent,
} from "react";
import {
  AlertTriangle,
  CircleDashed,
  Cpu,
  File,
  FileImage,
  FileSpreadsheet,
  FileText,
  Globe,
  Loader2,
  Lock,
  Mail,
  Presentation,
  Unlink,
  X,
  XCircle,
  Clock,
  CheckCircle2,
  CloudOff,
  type LucideIcon,
} from "lucide-react";
import type { DocStatus, TrayStatus } from "./api";
import { currentToast, dismissToast, subscribeToToast } from "./toast";

export function PrivateBadge({
  label = "Private",
}: Readonly<{
  label?: string;
}>): ReactElement {
  return (
    <span className="badge private">
      <Lock aria-hidden="true" />
      {label}
    </span>
  );
}

export function LocalBadge({ tag }: Readonly<{ tag?: string }>): ReactElement {
  return (
    <span className="badge local">
      <Cpu aria-hidden="true" />
      Local AI{tag ? ` · ${tag}` : ""}
    </span>
  );
}

const stateMeta: Partial<
  Record<DocStatus, { cls: string; label: string; Icon: LucideIcon }>
> = {
  queued: { cls: "queued", label: "Queued", Icon: Clock },
  indexing: { cls: "indexing", label: "Indexing", Icon: Loader2 },
  missing: { cls: "missing", label: "Missing", Icon: Unlink },
  not_downloaded: { cls: "skipped", label: "Not downloaded", Icon: CloudOff },
  failed: { cls: "failed", label: "Failed", Icon: XCircle },
  cancelled: { cls: "skipped", label: "Cancelled", Icon: X },
  skipped_password: { cls: "skipped", label: "Needs password", Icon: Lock },
  skipped_unsupported: {
    cls: "skipped",
    label: "Skipped",
    Icon: AlertTriangle,
  },
};

/** A document's import state. Indexed shows no badge. */
export function StateBadge({
  state,
}: Readonly<{
  state: DocStatus;
}>): ReactElement | null {
  const m = stateMeta[state];
  if (!m) return null;
  return (
    <span className={`badge ${m.cls}`}>
      <m.Icon aria-hidden="true" />
      {m.label}
    </span>
  );
}

const importMeta: Record<TrayStatus, { cls: string; Icon: LucideIcon }> = {
  Queued: { cls: "queued", Icon: Clock },
  Indexing: { cls: "indexing", Icon: Loader2 },
  Completed: { cls: "completed", Icon: CheckCircle2 },
  Failed: { cls: "failed", Icon: XCircle },
  Skipped: { cls: "skipped", Icon: AlertTriangle },
};

export function ImportBadge({
  status,
}: Readonly<{ status: TrayStatus }>): ReactElement {
  const m = importMeta[status];
  return (
    <span className={`badge ${m.cls}`}>
      <m.Icon aria-hidden="true" />
      {status}
    </span>
  );
}

export function PendingBadge(): ReactElement {
  return (
    <span className="badge outline">
      <CircleDashed aria-hidden="true" />
      Card pending
    </span>
  );
}

const kindIcons: Record<string, LucideIcon> = {
  PDF: FileText,
  DOCX: FileText,
  MD: FileText,
  TXT: FileText,
  XLSX: FileSpreadsheet,
  CSV: FileSpreadsheet,
  PPTX: Presentation,
  EML: Mail,
  PNG: FileImage,
  JPEG: FileImage,
  HEIC: FileImage,
  HTML: Globe,
  HTM: Globe,
  WEBP: FileImage,
};

export function KindIcon({
  kind,
  size = 16,
}: Readonly<{
  kind: string;
  size?: number;
}>): ReactElement {
  const Icon = kindIcons[kind] ?? File;
  return <Icon size={size} aria-hidden="true" />;
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
  describedBy,
  hideLabel,
}: Readonly<{
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
  describedBy?: string;
  hideLabel?: boolean;
}>): ReactElement {
  const id = useId();
  return (
    <span className="switch">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={hideLabel ? label : undefined}
        aria-labelledby={hideLabel ? undefined : `${id}-l`}
        aria-describedby={describedBy}
        disabled={disabled}
        onClick={() => {
          onChange(!checked);
        }}
      />
      {!hideLabel && (
        <label id={`${id}-l`} htmlFor={id}>
          {label}
        </label>
      )}
    </span>
  );
}

export interface TabDef {
  id: string;
  label: string;
  count?: number | string;
}

/** WAI-ARIA tabs with arrow-key movement. */
export function Tabs({
  tabs,
  value,
  onChange,
  label,
  idBase,
}: Readonly<{
  tabs: TabDef[];
  value: string;
  onChange: (id: string) => void;
  label: string;
  idBase: string;
}>): ReactElement {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    const i = tabs.findIndex((t) => t.id === value);
    let n = -1;
    if (e.key === "ArrowRight") n = (i + 1) % tabs.length;
    if (e.key === "ArrowLeft") n = (i - 1 + tabs.length) % tabs.length;
    if (e.key === "Home") n = 0;
    if (e.key === "End") n = tabs.length - 1;
    if (n >= 0) {
      e.preventDefault();
      onChange(tabs[n].id);
      refs.current[n]?.focus();
    }
  };
  return (
    <div
      className="tabs"
      role="tablist"
      tabIndex={-1}
      aria-label={label}
      onKeyDown={onKey}
    >
      {tabs.map((t, i) => (
        <button
          key={t.id}
          ref={(el) => {
            refs.current[i] = el;
          }}
          role="tab"
          type="button"
          id={`${idBase}-tab-${t.id}`}
          aria-selected={t.id === value}
          aria-controls={`${idBase}-panel`}
          tabIndex={t.id === value ? 0 : -1}
          className="tab"
          onClick={() => {
            onChange(t.id);
          }}
        >
          {t.label}
          {t.count !== undefined && <span className="count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** A modal dialog that traps focus, closes on Escape and returns focus. */
function keepFocusInside(
  dialog: HTMLElement,
  e: globalThis.KeyboardEvent,
): void {
  const f = Array.from(
    dialog.querySelectorAll<HTMLElement>(
      "button:not(:disabled):not([tabindex='-1']), input, textarea, select, [tabindex='0'], a[href]",
    ),
  );
  const a = f.at(0);
  const z = f.at(-1);
  if (!a || !z) return;
  if (e.shiftKey && document.activeElement === a) {
    e.preventDefault();
    z.focus();
  } else if (!e.shiftKey && document.activeElement === z) {
    e.preventDefault();
    a.focus();
  }
}

export function Modal({
  title,
  onClose,
  children,
  footer,
  className,
  hideTitle,
  initialFocus,
}: Readonly<{
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
  hideTitle?: boolean;
  initialFocus?: string;
}>): ReactElement {
  const ref = useRef<HTMLDivElement>(null);
  const scrim = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const first = initialFocus
      ? el?.querySelector<HTMLElement>(initialFocus)
      : el?.querySelector<HTMLElement>(
          "input, textarea, button:not([aria-label='Close']):not([tabindex='-1'])",
        );
    (first ?? el)?.focus();
    return () => {
      prev?.focus();
    };
  }, [initialFocus]);
  const closing = useRef(onClose);
  useEffect(() => {
    closing.current = onClose;
  });
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closing.current();
      }
      if (e.key === "Tab" && ref.current) keepFocusInside(ref.current, e);
    };
    const backdrop = scrim.current;
    const onDown = (e: MouseEvent): void => {
      if (e.target === backdrop) closing.current();
    };
    document.addEventListener("keydown", onKey);
    backdrop?.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      backdrop?.removeEventListener("mousedown", onDown);
    };
  }, []);
  return (
    <div className="modal-scrim" ref={scrim}>
      <div
        ref={ref}
        className={`modal ${className ?? ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={id}
        tabIndex={-1}
      >
        <div className={hideTitle ? "sr-only" : "modal-head"}>
          <h2 id={id}>{title}</h2>
          {!hideTitle && (
            <button
              type="button"
              className="icon-btn"
              aria-label="Close"
              onClick={onClose}
            >
              <X size={16} aria-hidden="true" />
            </button>
          )}
        </div>
        {children}
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export interface MenuItemDef {
  label: string;
  onSelect: () => void;
  icon?: LucideIcon;
  danger?: boolean;
}

/** A button that opens a small menu (role=menu) with arrow-key movement. */
const FLOATING_MENU_HEIGHT = 140;

function besides(button: HTMLElement): CSSProperties {
  const at = button.getBoundingClientRect();
  const right = window.innerWidth - at.right;
  return at.bottom + FLOATING_MENU_HEIGHT > window.innerHeight
    ? { bottom: window.innerHeight - at.top + 4, right }
    : { top: at.bottom + 4, right };
}

export function MenuButton({
  label,
  items,
  children,
  className = "icon-btn",
  align = "right",
  floating = false,
}: Readonly<{
  label: string;
  items: MenuItemDef[];
  children: ReactNode;
  className?: string;
  align?: "left" | "right";
  /** Placed against the window, for a button inside something that scrolls. */
  floating?: boolean;
}>): ReactElement {
  const [open, setOpen] = useState(false);
  const [placed, setPlaced] = useState<CSSProperties>();
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (open)
      menu.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
  }, [open]);
  const close = (): void => {
    setOpen(false);
    btn.current?.focus();
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    const its = Array.from(
      menu.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? [],
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
      close();
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };
  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button
        ref={btn}
        type="button"
        className={className}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => {
          if (floating && btn.current) setPlaced(besides(btn.current));
          setOpen((o) => !o);
        }}
      >
        {children}
      </button>
      {open && (
        <div
          ref={menu}
          id={id}
          role="menu"
          tabIndex={-1}
          aria-label={label}
          className="popover"
          style={
            floating
              ? { ...placed, position: "fixed", minWidth: 180 }
              : { top: "100%", [align]: 0, marginTop: 4, minWidth: 180 }
          }
          onKeyDown={onKey}
          onBlur={(e) => {
            if (!menu.current?.contains(e.relatedTarget)) setOpen(false);
          }}
        >
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              role="menuitem"
              tabIndex={-1}
              className="menu-item"
              onClick={() => {
                it.onSelect();
                close();
              }}
            >
              {it.icon && <it.icon size={14} aria-hidden="true" />}
              {it.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

export function ToastHost(): ReactElement {
  const t = useSyncExternalStore(subscribeToToast, currentToast);
  return (
    <div role="status" aria-live="polite">
      {t && (
        <div className="toast">
          {t.text}
          {t.undo && (
            <button
              type="button"
              onClick={() => {
                t.undo?.();
                dismissToast();
              }}
            >
              Undo
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function Skeleton({
  w = "100%",
  h = 12,
}: Readonly<{
  w?: string | number;
  h?: number;
}>): ReactElement {
  return (
    <div
      className="skeleton"
      style={{ width: w, height: h }}
      aria-hidden="true"
    />
  );
}
