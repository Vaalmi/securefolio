import type { ReactElement, ReactNode } from "react";

export function Row({
  title,
  desc,
  children,
}: Readonly<{
  title: string;
  desc?: ReactNode;
  children?: ReactNode;
}>): ReactElement {
  return (
    <div className="setting-row">
      <div className="s-text">
        <strong style={{ fontWeight: 500 }}>{title}</strong>
        {desc && (
          <span className="small" style={{ color: "var(--ink-2)" }}>
            {desc}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}
