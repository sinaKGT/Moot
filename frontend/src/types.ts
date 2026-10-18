// The server's data, as the app uses it (a subset of the API's models).

export type SpeakerKind = "agent" | "moderator" | "user" | "system";
export type Purpose = "opening" | "summary" | "conclusion";
export type Verdict = "yes" | "no" | "unsure";
export type WaitingFor = "start_round" | "next_speaker" | "finish";

export interface Persona {
  id: string;
  name: string;
  color: string;
  role: string;
  viewpoint: string;
}

export interface Round {
  id: string;
  type: string;
  name: string;
  instruction: string;
  speaking_order: "fixed" | "moderator" | "free";
  turns_per_agent: number;
  blind: boolean;
  mentions_allowed: boolean;
}

export type Moderator =
  | { kind: "ai"; name: string }
  | { kind: "human"; display_name: string; role: string };

export interface SessionConfig {
  topic: string;
  background: string;
  agents: Persona[];
  moderator: Moderator;
  round_plan: { rounds: Round[] };
}

export interface ChatMessage {
  id: string;
  speaker_id: string;
  speaker_kind: SpeakerKind;
  text: string;
  round_index: number | null;
  mentions: string[];
  reply_to: string | null;
  usage: { prompt_tokens: number; completion_tokens: number } | null;
}

export interface SessionSummary {
  id: string;
  topic: string;
  status: "setup" | "running" | "paused" | "finished";
  created_at: string;
  current_round: number | null;
  rounds: number;
  agents: string[];
  moderator: string;
  human_moderator: boolean;
  waiting_for: WaitingFor | null;
  error: string | null;
}

export interface SessionView {
  summary: SessionSummary;
  state: { config: SessionConfig };
}

export interface ProviderInfo {
  id: string;
  label: string;
  local: boolean;
  base_url: string | null;
  api_key_env: string | null;
  key_set: boolean;
}

export interface ModelEntry {
  id: string;
  name: string | null;
}

export interface PollOption {
  id: string;
  label: string;
  agent_id: string | null;
}

export interface Poll {
  id: string;
  question: string;
  kind: "winner" | "choice";
  options: PollOption[];
  eligible_voters: string[];
  status: "open" | "closed";
  opened_by: string;
  round_index: number | null;
  previous_id: string | null;
}

export interface PollResult {
  option_id: string;
  label: string;
  votes: number;
  percent: number;
  voters: string[];
}

export interface Alliance {
  id: string;
  goal: string;
  proposed_by: string;
  members: string[];
  invited: string[];
  status: "proposed" | "active" | "dissolved";
}

export interface GraphLink {
  type: "supports" | "challenges" | "answers" | "refines" | "concedes_to";
  source_speaker: string | null;
  target_speaker: string | null;
  message_id: string | null;
}

interface Base {
  seq: number;
}

export type MootEvent = Base &
  (
    | { type: "session_started"; session_id: string }
    | { type: "session_paused" }
    | { type: "session_resumed" }
    | { type: "session_ended"; reason: "completed" | "stopped" | "error" }
    | { type: "round_started"; round_index: number; round_id: string }
    | { type: "round_ended"; round_index: number }
    | {
        type: "turn_started";
        message_id: string;
        speaker_id: string;
        round_index: number | null;
        reply_to: string | null;
        purpose: Purpose | null;
      }
    | { type: "token_delta"; message_id: string; text: string }
    | { type: "turn_completed"; message: ChatMessage }
    | { type: "moderator_decision"; next_speaker_id: string | null; reason: string }
    | { type: "graph_updated"; links: GraphLink[] }
    | { type: "notebook_updated"; agent_id: string; verdict: Verdict | null; confidence: number | null }
    | {
        type: "alliance_changed";
        change: "proposed" | "accepted" | "refused" | "left" | "dissolved";
        agent_id: string | null;
        targets: string[];
        reason: string;
        alliance: Alliance;
      }
    | { type: "poll_opened"; poll: Poll }
    | { type: "vote_cast"; poll_id: string; voter_id: string; option_id: string }
    | { type: "poll_closed"; poll_id: string; results: PollResult[] }
    | { type: "rule_violation"; agent_id: string; rule: string }
    | { type: "engine_notice"; message: string }
    | { type: "waiting_for_moderator"; waiting_for: WaitingFor; round_index: number | null }
    | { type: "error"; message: string; agent_id: string | null }
  );

export const EVENT_TYPES: MootEvent["type"][] = [
  "session_started",
  "session_paused",
  "session_resumed",
  "session_ended",
  "round_started",
  "round_ended",
  "turn_started",
  "token_delta",
  "turn_completed",
  "moderator_decision",
  "graph_updated",
  "notebook_updated",
  "alliance_changed",
  "poll_opened",
  "vote_cast",
  "poll_closed",
  "rule_violation",
  "engine_notice",
  "waiting_for_moderator",
  "error",
];
