// Which documents are pictures, shown by ImageView rather than as pages or text.

const PICTURE_KINDS = new Set(["PNG", "JPEG", "WEBP", "HEIC"]);

export function isPicture(kind: string): boolean {
  return PICTURE_KINDS.has(kind);
}
