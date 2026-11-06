import type { ReactElement } from "react";
import { ChevronDown } from "lucide-react";

import { MenuButton } from "../ui";

export function Facet({
  label,
  value,
  options,
  onPick,
}: Readonly<{
  label: string;
  value: string | null;
  options: { value: string; label: string }[];
  onPick: (value: string | null) => void;
}>): ReactElement {
  const shown = options.find((o) => o.value === value)?.label ?? value;
  return (
    <MenuButton
      label={
        value
          ? `${label}: ${shown ?? ""}. Change filter`
          : `Filter by ${label.toLowerCase()}`
      }
      className={`chip ${value ? "active" : ""}`}
      align="left"
      items={[
        ...(value
          ? [
              {
                label: `Any ${label.toLowerCase()}`,
                onSelect: () => {
                  onPick(null);
                },
              },
            ]
          : []),
        ...options.map((option) => ({
          label: option.label,
          onSelect: () => {
            onPick(option.value);
          },
        })),
      ]}
    >
      {value ? `${label}: ${shown ?? ""}` : label}
      <ChevronDown size={14} aria-hidden="true" />
    </MenuButton>
  );
}
