// Thin typed client for the Grabbit REST API. All paths are relative so the
// SPA works at the domain root or under a reverse-proxy sub-path.

export type JobState = "queued" | "active" | "paused" | "done" | "error" | "cancelled";

export interface Job {
  id: number;
  url: string;
  host: string;
  state: JobState;
  dest: string;
  files_total: number;
  files_done: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  dir_name: string;
  rename_to: string | null;
}

export interface SubmitResult {
  url: string;
  accepted: boolean;
  job_id: number | null;
  reason: string | null;
}

export interface Stats {
  queue: Record<string, number>;
  active: number;
  queued: number;
  disk_free_bytes: number;
  disk_total_bytes: number;
  version: string;
  public_url: string | null;
}

export interface ApiKeyInfo {
  id: number;
  name: string;
  scope: "submit" | "admin";
  created_at: string;
  last_used_at: string | null;
}

export interface ApiKeyCreated extends ApiKeyInfo {
  token: string;
}

export interface Settings {
  version: string;
  read_only: Record<string, Record<string, unknown> | string>;
  editable: Record<string, unknown>;
}

const TOKEN_KEY = "grabbit_token";

export const getToken = (): string => localStorage.getItem(TOKEN_KEY) ?? "";
export const setToken = (t: string): void => localStorage.setItem(TOKEN_KEY, t);

// Base path of the app mount (e.g. "/grabbit/"), derived from where index.html
// was served; window.location works both at root and under a sub-path.
const base = new URL(".", window.location.href).pathname;

export const apiUrl = (path: string): string => `${base}api/${path}`;

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(apiUrl(path), {
    ...init,
    headers: {
      Authorization: `Bearer ${getToken()}`,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  submit: (urls: string[], dest?: string) =>
    request<SubmitResult[]>("downloads", {
      method: "POST",
      body: JSON.stringify({ urls, ...(dest ? { dest } : {}) }),
    }),
  listJobs: (state?: JobState) =>
    request<Job[]>(`downloads${state ? `?state=${state}` : ""}`),
  pause: (id: number) => request<Job>(`downloads/${id}/pause`, { method: "POST" }),
  resume: (id: number) => request<Job>(`downloads/${id}/resume`, { method: "POST" }),
  retry: (id: number) => request<Job>(`downloads/${id}/retry`, { method: "POST" }),
  rename: (id: number, name: string) =>
    request<Job>(`downloads/${id}/rename`, { method: "POST", body: JSON.stringify({ name }) }),
  merge: (jobIds: number[], name: string) =>
    request<Job[]>("downloads/merge", {
      method: "POST",
      body: JSON.stringify({ job_ids: jobIds, name }),
    }),
  remove: (id: number) => request<void>(`downloads/${id}`, { method: "DELETE" }),
  stats: () => request<Stats>("stats"),
  settings: () => request<Settings>("settings"),
  listKeys: () => request<ApiKeyInfo[]>("keys"),
  createKey: (name: string, scope: "submit" | "admin") =>
    request<ApiKeyCreated>("keys", { method: "POST", body: JSON.stringify({ name, scope }) }),
  deleteKey: (id: number) => request<void>(`keys/${id}`, { method: "DELETE" }),
  // Admin-scoped: the server mints a submit key and bakes it into the zip.
  downloadExtension: async (): Promise<void> => {
    const res = await fetch(apiUrl("extension.zip"), {
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {
        /* not json */
      }
      throw new ApiError(res.status, detail);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "grabbit-extension.zip";
    a.click();
    URL.revokeObjectURL(url);
  },
};

export type WsEvent =
  | { type: "state"; job_id: number; state: JobState; error?: string; files_done?: number }
  | { type: "progress"; job_id: number; files_done: number; current_file: string | null };

export function openEventSocket(onEvent: (e: WsEvent) => void, onDrop: () => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry = 1000;

  const connect = () => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}${base}api/ws?token=${encodeURIComponent(getToken())}`;
    ws = new WebSocket(url);
    ws.onmessage = (msg) => {
      retry = 1000;
      onEvent(JSON.parse(msg.data));
    };
    ws.onclose = () => {
      if (closed) return;
      onDrop();
      setTimeout(connect, retry);
      retry = Math.min(retry * 2, 15000);
    };
  };
  connect();
  return () => {
    closed = true;
    ws?.close();
  };
}
