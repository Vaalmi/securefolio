import { connectionInfo } from "./desktop";
import type { Connection } from "./desktop";

// How the app reaches the backend: its address and token, sending a request, and
// reading an event stream with fetch. Only api.ts uses it.

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

let current: Promise<Connection> | undefined;

export function connection(): Promise<Connection> {
  current ??= connectionInfo();
  return current;
}

// After the backend restarts, its port and token are new.
export function forgetConnection(): void {
  current = undefined;
}

function reasonFor(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return "Some of what was sent isn't valid.";
  return "Something went wrong.";
}

async function send(path: string, init: RequestInit): Promise<Response> {
  const { base, token } = await connection();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${base}/api${path}`, { ...init, headers });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    throw new ApiError(response.status, reasonFor(body.detail));
  }
  return response;
}

export async function get<T>(path: string): Promise<T> {
  const response = await send(path, {});
  return (await response.json()) as T;
}

export async function getBytes(path: string): Promise<ArrayBuffer> {
  const response = await send(path, {});
  return response.arrayBuffer();
}

function withBody(method: string, body?: object): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

export async function post<T>(path: string, body?: object): Promise<T> {
  const response = await send(path, withBody("POST", body));
  return (await response.json()) as T;
}

export async function patch<T>(path: string, body: object): Promise<T> {
  const response = await send(path, withBody("PATCH", body));
  return (await response.json()) as T;
}

export async function put<T>(path: string, body: object): Promise<T> {
  const response = await send(path, withBody("PUT", body));
  return (await response.json()) as T;
}

export async function del<T>(path: string): Promise<T> {
  const response = await send(path, { method: "DELETE" });
  return (await response.json()) as T;
}

export interface StreamEvent {
  event: string;
  data: unknown;
}

// Splits a server-sent event stream's text into events, keeping what isn't
// complete yet for the next chunk.
export function parseEvents(buffer: string): {
  events: StreamEvent[];
  rest: string;
} {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  const events: StreamEvent[] = [];
  for (const part of parts) {
    let event = "message";
    const data: string[] = [];
    for (const line of part.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data.push(line.slice(5).trim());
    }
    if (data.length > 0) {
      events.push({ event, data: JSON.parse(data.join("\n")) as unknown });
    }
  }
  return { events, rest };
}

// Reads a stream until it ends or `signal` aborts, handing over each event.
export async function stream(
  path: string,
  onEvent: (event: StreamEvent) => void,
  options: { method?: string; body?: object; signal?: AbortSignal } = {},
): Promise<void> {
  const init =
    options.method === "POST"
      ? withBody("POST", options.body)
      : { method: "GET" };
  const response = await send(path, { ...init, signal: options.signal });
  const reader = response.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseEvents(buffer);
    buffer = rest;
    events.forEach(onEvent);
  }
}
