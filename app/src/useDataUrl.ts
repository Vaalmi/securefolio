import { useLoad } from "./useLoad";
import type { Loaded } from "./useLoad";

function asDataUrl(bytes: ArrayBuffer): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      resolve(reader.result as string);
    };
    reader.onerror = () => {
      reject(new Error("This picture couldn't be read."));
    };
    reader.readAsDataURL(new Blob([bytes]));
  });
}

// A picture from a route that needs the token, as an <img> can't send one.
export function useDataUrl(
  load: () => Promise<ArrayBuffer>,
  key: string,
): Loaded<string> {
  return useLoad(() => load().then(asDataUrl), key);
}
