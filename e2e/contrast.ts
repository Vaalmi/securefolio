// WCAG AA contrast for every visible text and background pair (4.5:1, or 3:1 for large
// text), and for control boundaries (3:1). It runs inside the page, so it all stays in
// one function.

type Rgba = [number, number, number, number];

export function auditContrast(): string[] {
  const parse = (color: string): Rgba => {
    const m = /rgba?\(([^)]+)\)/.exec(color);
    if (!m) return [0, 0, 0, 0];
    const p = m[1]
      .split(/[ ,/]+/)
      .filter(Boolean)
      .map(Number);
    return [p.at(0) ?? 0, p.at(1) ?? 0, p.at(2) ?? 0, p.at(3) ?? 1];
  };
  const blend = (top: Rgba, bottom: Rgba): Rgba => {
    const a = top[3];
    return [
      top[0] * a + bottom[0] * (1 - a),
      top[1] * a + bottom[1] * (1 - a),
      top[2] * a + bottom[2] * (1 - a),
      1,
    ];
  };
  const channel = (value: number): number => {
    const v = value / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const luminance = (c: Rgba): number =>
    0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2]);
  const ratio = (a: Rgba, b: Rgba): number => {
    const x = luminance(a);
    const y = luminance(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const over = (color: Rgba, below: Rgba): Rgba =>
    color[3] < 1 ? blend(color, below) : color;
  const backgroundOf = (element: Element | null): Rgba => {
    const layers: Rgba[] = [];
    let opaque = false;
    for (let e = element; e && !opaque; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (c[3] > 0) layers.unshift(c);
      opaque = c[3] >= 1;
    }
    const base: Rgba = opaque
      ? [255, 255, 255, 1]
      : parse(getComputedStyle(document.body).backgroundColor);
    return layers.reduce((acc, layer) => blend(layer, acc), base);
  };
  const shown = (e: Element): boolean => {
    const s = getComputedStyle(e);
    return (
      s.visibility !== "hidden" &&
      s.display !== "none" &&
      s.opacity !== "0" &&
      !e.classList.contains("sr-only")
    );
  };
  const visible = (element: Element): boolean => {
    const r = element.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    for (let e: Element | null = element; e; e = e.parentElement) {
      if (!shown(e)) return false;
    }
    return (
      r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth
    );
  };
  const textFailure = (element: Element): string | null => {
    const style = getComputedStyle(element);
    const background = backgroundOf(element);
    const size = parseFloat(style.fontSize);
    const large =
      size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
    const need = large ? 3 : 4.5;
    const found = ratio(over(parse(style.color), background), background);
    if (found >= need - 0.005) return null;
    const words = element.textContent.trim().slice(0, 40);
    return `text "${words}" ${found.toFixed(2)} < ${String(need)}`;
  };
  const controlFailure = (element: Element): string | null => {
    const style = getComputedStyle(element);
    const around = backgroundOf(element.parentElement);
    const fill = parse(style.backgroundColor);
    const fillOver = fill[3] > 0 ? blend(fill, around) : around;
    const border = over(parse(style.borderTopColor), around);
    const borderRatio =
      parseFloat(style.borderTopWidth) > 0 ? ratio(border, around) : 0;
    const best = Math.max(borderRatio, ratio(fillOver, around));
    return best < 3
      ? `control ${element.className} ${best.toFixed(2)} < 3`
      : null;
  };
  const hasOwnText = (element: Element): boolean =>
    [...element.childNodes].some(
      (n) => n.nodeType === 3 && n.textContent?.trim(),
    );
  const disabled = (element: Element): boolean =>
    element.closest("button:disabled, [aria-disabled=true], input:disabled") !==
    null;
  const texts = [...document.querySelectorAll("body *")]
    .filter((e) => hasOwnText(e) && !disabled(e) && visible(e))
    .map(textFailure);
  const controls = [
    ...document.querySelectorAll(
      ".btn:not(.ghost), .input, .select, .textarea, .chip, .seg, .composer, .radio-card, .cbox",
    ),
  ]
    .filter((e) => visible(e) && !e.matches(":disabled"))
    .map(controlFailure);
  return [...texts, ...controls].filter((fail) => fail !== null);
}
