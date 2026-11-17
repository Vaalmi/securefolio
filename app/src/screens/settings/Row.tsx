import type { ReactElement, ReactNode } from "react";

export function Row({
  title,
  desc,
  below,
  children,
}: Readonly<{
  title: string;
  desc?: ReactNode;
  below?: ReactNode; // under the words, beside the control: a model's scores, say
  children?: ReactNode;
}>): ReactElement {
  return (
    <div className={below ? "setting-row top" : "setting-row"}>
      <div className="s-text">
        <strong style={{ fontWeight: 500 }}>{title}</strong>
        {desc && (
          <span className="small" style={{ color: "var(--ink-2)" }}>
            {desc}
          </span>
        )}
        {below}
      </div>
      {children}
    </div>
  );
}
