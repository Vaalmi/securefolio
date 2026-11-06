import type { ReactElement, ReactNode } from "react";
import { Check } from "lucide-react";

import { Logo } from "../components/Logo";

const STEPS = ["Cloud key", "Search models", "Folders", "Ready"];

function Stepper({ at }: Readonly<{ at: number }>): ReactElement {
  return (
    <ol className="stepper" aria-label="Setup steps">
      {STEPS.map((name, i) => (
        <li
          key={name}
          aria-current={i === at ? "step" : undefined}
          className={i < at ? "done" : undefined}
        >
          <span className="n" aria-hidden="true">
            {i < at ? <Check size={12} /> : i + 1}
          </span>
          {name}
          {i < at && <span className="sr-only">, done</span>}
        </li>
      ))}
    </ol>
  );
}

export function SetupShell({
  at,
  children,
}: Readonly<{ at: number; children: ReactNode }>): ReactElement {
  return (
    <main className="setup" id="main">
      <div className="hstack" style={{ fontWeight: 600, fontSize: 18 }}>
        <Logo size={28} /> SecureFolio
      </div>
      <Stepper at={at} />
      <div className="setup-card">{children}</div>
    </main>
  );
}
