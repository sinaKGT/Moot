// The server's data, as the app uses it (a subset of the API's models).

export type SpeakerKind = "agent" | "moderator" | "user" | "system";
export type Purpose = "opening" | "summary" | "conclusion";
export type Verdict = "yes" | "no" | "unsure";
export type WaitingFor = "start_round" | "next_speaker" | "finish";

export interface ModelRef {
  provider: string;
  model: string;
  temperature?: number | null;
}

export interface Traits {
  persuadability: number;
  verbosity: number;
  alliance_openness: number;
}

export interface Persona {
  id: string;
  name: string;
  color: string;
  role: string;
  viewpoint: string;
  motivation: string;
  expertise: string;
  thinking_style: string;
  traits: Traits;
  model: ModelRef | null;
  prompt_override: string | null;
  knowledge?: string | null;
}

export interface KnowledgeDocument {
  id: string;
  filename: string;
  bytes: number;
  created_at: string;
  status: "queued" | "indexing" | "ready" | "failed";
  stage: string;
  chunks_total: number;
  chunks_mapped: number;
  chunks_failed: number;
  error: string | null;
  notes: string[];
}

export interface KnowledgeBase {
  id: string;
  name: string;
  created_at: string;
  embedding: ModelRef | null;
  documents: KnowledgeDocument[];
  passages: number;
  entities: number;
  facts: number;
}

export interface KnowledgeSearch {
  facts: { statement: string; source: string }[];
  passages: { text: string; source: string }[];
  briefing: string;
}

export type RoundType = "opening" | "challenge" | "qa" | "rebuttal" | "free" | "vote" | "closing";
export type SpeakingOrder = "fixed" | "moderator" | "free";

export interface Round {
  id: string;
  type: RoundType;
  name: string;
  instruction: string;
  speaking_order: SpeakingOrder;
  turns_per_agent: number;
  blind: boolean;
  mentions_allowed: boolean;
  max_words: number | null;
}

export interface SessionRules {
  presets: string[];
  custom: string[];
  max_words_per_turn: number | null;
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
  rules: SessionRules;
  options: string[];
  default_model: ModelRef | null;
  allow_alliances: boolean;
}

/** What the Team Designer proposes (POST /api/designer). */
export interface TeamProposal {
  topic: string;
  format: string;
  options: string[];
  agents: Persona[];
  rationale: string;
  balance_notes: string[];
  revised: boolean;
}

export interface Library {
  personas: Persona[];
  templates: Record<string, { name: string; plan: { rounds: Round[] } }>;
  rule_presets: Record<string, string>;
  global_rules: string[];
  example: { topic: string; agents: Persona[]; round_plan: { rounds: Round[] }; rules: SessionRules };
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
  model?: string | null;
  seconds?: number | null;
  sources?: string[];
}

export interface SessionSummary {
  id: string;
  topic: string;
  status: "setup" | "running" | "paused" | "finished";
  end_reason?: "completed" | "stopped" | "error" | null;
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

export type AllianceChange = "proposed" | "accepted" | "refused" | "left" | "dissolved";

export interface AllianceStep {
  change: AllianceChange;
  agent_id: string | null;
  targets: string[];
  round_index: number | null;
  reason: string;
}

export interface Alliance {
  id: string;
  goal: string;
  proposed_by: string;
  members: string[];
  invited: string[];
  refused?: string[];
  left?: string[];
  status: "proposed" | "active" | "dissolved";
  history?: AllianceStep[];
}

export type Side = "for" | "against" | "mixed";

/** What the alliance monitor shows, up to a round (GET /api/sessions/{id}/alliances). */
export interface AllianceMonitor {
  up_to_round: number | null;
  relations: { source: string; target: string; support: number; challenge: number }[];
  pairs: { first: string; second: string; support: number; challenge: number; allied: boolean }[];
  sides: Record<string, Side | null>;
  alliances: Alliance[];
  mismatches: { alliance_id: string; challenger: string; ally: string; challenges: number; supports: number }[];
}

export type NodeType = "topic" | "subquestion" | "claim" | "evidence" | "question" | "concession";
export type EdgeType = "supports" | "challenges" | "answers" | "refines" | "concedes_to";

export interface GraphNode {
  id: string;
  type: NodeType;
  text: string;
  speaker_id: string | null;
  round_index: number | null;
  message_id: string | null;
  addressed_to: string[];
  sources: string[];
}

export interface GraphEdge {
  id: string;
  type: EdgeType;
  source: string;
  target: string;
  speaker_id: string | null;
}

export interface ArgumentGraph {
  nodes: Record<string, GraphNode>;
  edges: GraphEdge[];
}

export interface GraphLink {
  type: EdgeType;
  source_speaker: string | null;
  target_speaker: string | null;
  message_id: string | null;
}

interface Base {
  seq: number;
}

export type MootEvent = Base &
  (
    | { type: "session_started"; session_id: string; graph?: ArgumentGraph }
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
    | { type: "graph_updated"; links: GraphLink[]; nodes?: GraphNode[]; edges?: GraphEdge[] }
    | {
        type: "notebook_updated";
        agent_id: string;
        verdict: Verdict | null;
        choice: string | null;
        confidence: number | null;
      }
    | {
        type: "alliance_changed";
        change: AllianceChange;
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
