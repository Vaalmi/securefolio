import type { Collection, ThreadScope } from "./api";
import type { ScopeOption } from "./components/ScopeChip";

const ALL: ThreadScope = {
  kind: "library",
  include_private: false,
  label: "All · Private excluded",
};
export const ALL_PRIVATE: ThreadScope = {
  kind: "library",
  include_private: true,
  label: "All · Private included",
};

export function scopeFromParam(
  param: string | null,
  collections: Collection[],
): ThreadScope {
  if (param === "all-private") return ALL_PRIVATE;
  if (param?.startsWith("sel:")) {
    const ids = param.slice(4).split(",").filter(Boolean);
    return {
      kind: "documents",
      ids,
      label:
        ids.length === 1
          ? "This document"
          : `${String(ids.length)} selected documents`,
    };
  }
  const collection = collections.find((c) => c.id === param);
  if (collection) {
    return {
      kind: "collection",
      id: collection.id,
      label: `Collection: ${collection.name}`,
    };
  }
  return ALL;
}

export function optionOf(scope: ThreadScope): ScopeOption {
  return {
    id: JSON.stringify(scope),
    label: scope.label,
    private: scope.kind === "library" && scope.include_private,
  };
}

export function scopeOptions(
  current: ThreadScope,
  collections: Collection[],
): { option: ScopeOption; scope: ThreadScope }[] {
  const scopes: ThreadScope[] = [
    current,
    ...[ALL, ALL_PRIVATE].filter((s) => s.label !== current.label),
    ...collections
      .filter(
        (c) =>
          c.count > 0 &&
          c.id !== (current.kind === "collection" ? current.id : ""),
      )
      .map((c): ThreadScope => ({
        kind: "collection",
        id: c.id,
        label: `Collection: ${c.name}`,
      })),
  ];
  return scopes.map((scope) => ({ option: optionOf(scope), scope }));
}
