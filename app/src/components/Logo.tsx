import type { ReactElement } from "react";

import logoTile from "../assets/securefolio_logo_bg.svg";

// SecureFolio's mark: the logo on its indigo square, in light and dark mode alike, since
// the white logo alone wouldn't show on the light sidebar. It sits beside the word
// "SecureFolio", so it's decorative.
export function Logo({ size }: Readonly<{ size: number }>): ReactElement {
  return (
    <img
      className="logo-tile"
      src={logoTile}
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
    />
  );
}
