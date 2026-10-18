// Talking to the Moot server.

import type { ModelEntry, ProviderInfo, SessionSummary, SessionView } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // not JSON: keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export type Control = "start" | "pause" | "resume" | "stop" | "finish" | "next-round" | "end-round";

export interface DemoRequest {
  topic?: string;
  provider: string;
  model: string;
  moderator?: { kind: "human"; display_name: string; role: string };
}

export const api = {
  providers: () => request<ProviderInfo[]>("/api/providers"),
  models: (provider: string) =>
    request<ModelEntry[]>(`/api/providers/${encodeURIComponent(provider)}/models`),
  sessions: () => request<SessionSummary[]>("/api/sessions"),
  session: (id: string) => request<SessionView>(`/api/sessions/${id}`),
  createDemo: (body: DemoRequest) => post<SessionSummary>("/api/demo", body),
  control: (id: string, control: Control) => post<SessionSummary>(`/api/sessions/${id}/${control}`),
  say: (id: string, text: string) => post<SessionSummary>(`/api/sessions/${id}/messages`, { text }),
  floor: (id: string, agent: string | null) =>
    post<SessionSummary>(`/api/sessions/${id}/floor`, { agent }),
  remind: (id: string, agent: string, text: string) =>
    post<SessionSummary>(`/api/sessions/${id}/reminders`, { agent, text }),
  poll: (id: string, question?: string, options?: string[]) =>
    post<SessionSummary>(`/api/sessions/${id}/polls`, question ? { question, options } : {}),
};

export const eventsUrl = (id: string) => `/api/sessions/${id}/events`;
