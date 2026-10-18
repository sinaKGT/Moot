// Talking to the Moot server.

import type {
  AllianceMonitor,
  Library,
  ModelEntry,
  ProviderInfo,
  SessionSummary,
  SessionView,
  TeamProposal,
} from "./types";

export interface DesignRequest {
  goal: string;
  provider: string;
  model: string;
  agents: number | null;
  balance_check: boolean;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

interface ValidationProblem {
  loc?: (string | number)[];
  msg?: string;
}

/** "body.agents.1.name: Value error, ..." -> "Agent 2 · name: ..." */
export function describeProblem(problem: ValidationProblem): string {
  const path = (problem.loc ?? []).filter((part) => part !== "body");
  const words: string[] = [];
  for (let i = 0; i < path.length; i++) {
    const part = path[i];
    const next = path[i + 1];
    if ((part === "agents" || part === "rounds") && typeof next === "number") {
      words.push(`${part === "agents" ? "Agent" : "Round"} ${next + 1}`);
      i++;
    } else if (part !== "round_plan") {
      words.push(String(part).replace(/_/g, " "));
    }
  }
  const message = (problem.msg ?? "is not valid").replace(/^Value error, /, "");
  return words.length ? `${words.join(" · ")}: ${message}` : message;
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
      detail = Array.isArray(body.detail)
        ? body.detail.map(describeProblem).join("\n")
        : typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail);
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
  deleteSession: (id: string) => request<void>(`/api/sessions/${id}`, { method: "DELETE" }),
  createDemo: (body: DemoRequest) => post<SessionSummary>("/api/demo", body),
  createSession: (config: unknown) => post<SessionSummary>("/api/sessions", config),
  library: () => request<Library>("/api/library"),
  design: (body: DesignRequest) => post<TeamProposal>("/api/designer", body),
  alliances: (id: string, upToRound: number | null) =>
    request<AllianceMonitor>(`/api/sessions/${id}/alliances${upToRound === null ? "" : `?up_to_round=${upToRound}`}`),
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
export const exportUrl = (id: string, format: "markdown" | "json") => `/api/sessions/${id}/export?format=${format}`;
