import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import type { Mode } from "./api";
import { AppContext } from "./storeContext";

export function AppProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [mode, setMode] = useState<Mode>("standard");
  const [includePrivate, setIncludePrivate] = useState(false);
  const value = useMemo(
    () => ({ mode, setMode, includePrivate, setIncludePrivate }),
    [mode, includePrivate],
  );
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
