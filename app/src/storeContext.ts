import { createContext, useContext } from "react";

import type { Mode } from "./api";

export interface AppState {
  mode: Mode;
  setMode: (mode: Mode) => void;
  includePrivate: boolean;
  setIncludePrivate: (value: boolean) => void;
}

export const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const value = useContext(AppContext);
  if (!value) throw new Error("useApp outside AppProvider");
  return value;
}
