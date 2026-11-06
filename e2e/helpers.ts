import { copyFileSync, mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";

import { BACKEND, VAULT } from "./servers";

export const FIXTURES = resolve("e2e/.fixtures");
export const LIBRARY = resolve("data/library");

function token(): string {
  const runtime = JSON.parse(readFileSync(`${VAULT}/runtime.json`, "utf8")) as {
    app_token: string;
  };
  return runtime.app_token;
}

export async function api<T>(
  path: string,
  method = "GET",
  body?: object,
): Promise<T> {
  const response = await fetch(`${BACKEND}/api${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token()}`,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`${method} ${path}: ${String(response.status)}`);
  }
  return (await response.json()) as T;
}

export interface Row {
  id: string;
  title: string;
  file_name: string;
  file_kind: string;
  status: string;
  sensitivity: string;
}

export interface Hit {
  id: string;
  element_id: string;
  snippet: string;
  label: string;
  locator: {
    kind: string;
    page?: number;
    rects?: { x0: number; y0: number; x1: number; y1: number }[];
    precision?: string;
  };
  document: Row;
}

export async function documentNamed(fileName: string): Promise<Row> {
  const { rows } = await api<{ rows: Row[] }>(
    `/library?limit=500&name=${encodeURIComponent(fileName.replace(/\.[^.]+$/, ""))}`,
  );
  const found = rows.find((row) => row.file_name === fileName);
  if (!found) throw new Error(`No document called ${fileName}`);
  return found;
}

// A document that has just finished can take a moment to reach the search index.
export async function passage(marker: string, fileName: string): Promise<Hit> {
  let found: Hit | undefined;
  await expect
    .poll(
      async () => {
        const results = await api<{ passages: Hit[] }>(
          `/search?q=${encodeURIComponent(marker)}&include_private=true`,
        );
        found = results.passages.find(
          (hit) =>
            hit.document.file_name === fileName && hit.snippet.includes(marker),
        );
        return found !== undefined;
      },
      { message: `No passage with ${marker} in ${fileName}`, timeout: 15_000 },
    )
    .toBe(true);
  if (!found) throw new Error(`No passage with ${marker} in ${fileName}`);
  return found;
}

export async function queueDrained(): Promise<void> {
  await expect
    .poll(
      async () => {
        const { active } = await api<{ active: number }>("/jobs/counts");
        const sources = await api<{ status: string }[]>("/sources");
        return active + sources.filter((s) => s.status === "scanning").length;
      },
      { timeout: 60_000 },
    )
    .toBe(0);
}

export function fixtureFolder(name: string): string {
  const folder = `${FIXTURES}/${name}`;
  mkdirSync(folder, { recursive: true });
  return folder;
}

export function copyFromLibrary(relative: string, to: string): void {
  copyFileSync(`${LIBRARY}/${relative}`, to);
}

/** Fails the test if the page reaches anything but the app and the backend. */
export function watchRequests(page: Page): string[] {
  const off: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    const local = ["127.0.0.1", "localhost"].includes(url.hostname);
    const allowed = [new URL(BACKEND).port, "5199"].includes(url.port);
    if (!["data:", "blob:"].includes(url.protocol) && !(local && allowed)) {
      off.push(request.url());
    }
  });
  return off;
}
