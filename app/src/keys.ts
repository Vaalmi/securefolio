// The keys that ask, send and open. Keys that pick an input-method candidate never
// count: they finish composing a word.

interface KeyLike {
  key: string;
  shiftKey: boolean;
  metaKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
  nativeEvent?: { isComposing?: boolean; keyCode?: number };
}

// The key code an input method gives a key it handles. WebKit, the app's engine on
// the Mac, can send the Return that confirms a candidate with isComposing false and
// this code, so either one counts.
const IME_KEY_CODE = 229;

/** Whether this key picks an input-method candidate. */
export function isComposing(event: KeyLike): boolean {
  return (
    event.nativeEvent?.isComposing === true ||
    event.nativeEvent?.keyCode === IME_KEY_CODE
  );
}

/** Return alone: asks on Home and in quick search, and sends in a question box. */
export function isSendKey(event: KeyLike): boolean {
  return (
    event.key === "Enter" &&
    !event.shiftKey &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.altKey &&
    !isComposing(event)
  );
}

/** Command-Return, or Control-Return off the Mac: opens the selected result. */
export function isOpenKey(event: KeyLike): boolean {
  return (
    event.key === "Enter" &&
    (event.metaKey || event.ctrlKey) &&
    !isComposing(event)
  );
}
