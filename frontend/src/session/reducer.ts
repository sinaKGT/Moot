// Turns the live event stream into what the discussion screen shows.
// Pure: the same events always give the same state, so a reload simply replays them.

import type {
  Alliance,
  MootEvent,
  Poll,
  PollResult,
  Purpose,
  SpeakerKind,
  Verdict,
  WaitingFor,
} from "../types";

export type Tag = "challenge" | "support" | "question" | "answer";
export type NoticeTone = "system" | "alliance" | "decision" | "rule" | "error";
export type BeamKind = "challenge" | "support" | "question";

export interface MessageEntry {
  kind: "message";
  id: string;
  speakerId: string;
  speakerKind: SpeakerKind;
  text: string;
  replyTo: string | null;
  purpose: Purpose | null;
  roundIndex: number | null;
  done: boolean;
  mentions: string[];
  tags: Tag[];
  tokens: { in: number; out: number } | null;
}

export interface NoticeEntry {
  kind: "notice";
  id: string;
  text: string;
  tone: NoticeTone;
  agentId?: string;
}

export interface RoundEntry {
  kind: "round";
  id: string;
  roundIndex: number;
}

export interface PollEntry {
  kind: "poll";
  id: string;
  pollId: string;
}

export type Entry = MessageEntry | NoticeEntry | RoundEntry | PollEntry;

export interface Beam {
  key: string;
  from: string;
  to: string;
  kind: BeamKind;
}

export interface PollView {
  poll: Poll;
  votes: Record<string, string>;
  results: PollResult[] | null;
}

export interface DiscussionState {
  lastSeq: number;
  status: "setup" | "running" | "paused" | "finished";
  endReason: string | null;
  roundIndex: number | null;
  entries: Entry[];
  speakingId: string | null;
  waitingFor: { what: WaitingFor; roundIndex: number | null } | null;
  verdicts: Record<string, { verdict: Verdict | null; confidence: number | null }>;
  polls: Record<string, PollView>;
  activePollId: string | null;
  alliances: Record<string, Alliance>;
  beams: Beam[];
  conclusionId: string | null;
}

export const initialState: DiscussionState = {
  lastSeq: 0,
  status: "setup",
  endReason: null,
  roundIndex: null,
  entries: [],
  speakingId: null,
  waitingFor: null,
  verdicts: {},
  polls: {},
  activePollId: null,
  alliances: {},
  beams: [],
  conclusionId: null,
};

export const MAX_BEAMS = 6;
const SEATED: SpeakerKind[] = ["agent", "moderator"];

/** Speaker id -> display name. */
export type Names = Record<string, string>;

function nameOf(names: Names, id: string | null | undefined): string {
  return (id && names[id]) || id || "";
}

function list(names: Names, ids: string[]): string {
  const labels = ids.map((id) => nameOf(names, id));
  return labels.length > 1 ? `${labels.slice(0, -1).join(", ")} and ${labels.at(-1)}` : labels.join("");
}

function updateMessage(
  state: DiscussionState,
  id: string,
  change: (entry: MessageEntry) => MessageEntry,
): Entry[] {
  return state.entries.map((entry) =>
    entry.kind === "message" && entry.id === id ? change(entry) : entry,
  );
}

function addBeams(beams: Beam[], added: Beam[]): Beam[] {
  return [...beams, ...added].slice(-MAX_BEAMS);
}

function withTag(entry: MessageEntry, tag: Tag): MessageEntry {
  return entry.tags.includes(tag) ? entry : { ...entry, tags: [...entry.tags, tag] };
}

export function reduce(state: DiscussionState, event: MootEvent, names: Names): DiscussionState {
  if (event.seq <= state.lastSeq) return state; // already seen (a reconnect replays)
  const next = { ...state, lastSeq: event.seq };
  const notice = (text: string, tone: NoticeTone, agentId?: string): Entry[] => [
    ...next.entries,
    { kind: "notice", id: `n${event.seq}`, text, tone, agentId },
  ];

  switch (event.type) {
    case "session_started":
      return { ...next, status: "running" };
    case "session_paused":
      return { ...next, status: "paused" };
    case "session_resumed":
      return { ...next, status: "running" };
    case "session_ended":
      return { ...next, status: "finished", endReason: event.reason, speakingId: null, waitingFor: null };

    case "round_started": {
      const active = next.activePollId ? next.polls[next.activePollId] : null;
      return {
        ...next,
        roundIndex: event.round_index,
        waitingFor: null,
        activePollId: active && active.results ? null : next.activePollId,
        entries: [...next.entries, { kind: "round", id: `r${event.seq}`, roundIndex: event.round_index }],
      };
    }
    case "round_ended":
      return next;

    case "turn_started": {
      const exists = next.entries.some((e) => e.kind === "message" && e.id === event.message_id);
      const entry: MessageEntry = {
        kind: "message",
        id: event.message_id,
        speakerId: event.speaker_id,
        speakerKind: event.speaker_id === "moderator" ? "moderator" : event.speaker_id === "user" ? "user" : "agent",
        text: "",
        replyTo: event.reply_to,
        purpose: event.purpose,
        roundIndex: event.round_index,
        done: false,
        mentions: [],
        tags: event.reply_to ? ["answer"] : [],
        tokens: null,
      };
      return {
        ...next,
        speakingId: event.speaker_id === "user" ? next.speakingId : event.speaker_id,
        entries: exists ? next.entries : [...next.entries, entry],
      };
    }
    case "token_delta":
      return {
        ...next,
        entries: updateMessage(next, event.message_id, (e) => ({ ...e, text: e.text + event.text })),
      };
    case "turn_completed": {
      const m = event.message;
      if (m.speaker_kind === "system") return { ...next, entries: notice(m.text, "system") };
      const exists = next.entries.some((e) => e.kind === "message" && e.id === m.id);
      const base: MessageEntry = exists
        ? (next.entries.find((e) => e.kind === "message" && e.id === m.id) as MessageEntry)
        : {
            kind: "message",
            id: m.id,
            speakerId: m.speaker_id,
            speakerKind: m.speaker_kind,
            text: "",
            replyTo: m.reply_to,
            purpose: null,
            roundIndex: m.round_index,
            done: false,
            mentions: [],
            tags: m.reply_to ? ["answer"] : [],
            tokens: null,
          };
      let entry: MessageEntry = {
        ...base,
        speakerKind: m.speaker_kind,
        text: m.text,
        done: true,
        mentions: m.mentions,
        tokens: m.usage ? { in: m.usage.prompt_tokens, out: m.usage.completion_tokens } : null,
      };
      const asks = m.mentions.length > 0 && SEATED.includes(m.speaker_kind);
      if (asks && m.speaker_kind === "agent") entry = withTag(entry, "question");
      const beams = asks
        ? m.mentions.map((to) => ({ key: `q${event.seq}-${to}`, from: m.speaker_id, to, kind: "question" as const }))
        : [];
      return {
        ...next,
        speakingId: next.speakingId === m.speaker_id ? null : next.speakingId,
        entries: exists ? updateMessage(next, m.id, () => entry) : [...next.entries, entry],
        beams: addBeams(next.beams, beams),
        conclusionId: entry.purpose === "conclusion" ? m.id : next.conclusionId,
      };
    }

    case "moderator_decision":
      return {
        ...next,
        waitingFor: null,
        entries: event.next_speaker_id
          ? notice(
              `${nameOf(names, "moderator")} gives the floor to ${nameOf(names, event.next_speaker_id)}${
                event.reason ? `: ${event.reason}` : ""
              }`,
              "decision",
            )
          : next.entries,
      };

    case "graph_updated": {
      let entries = next.entries;
      const beams: Beam[] = [];
      event.links.forEach((link, i) => {
        const kind: BeamKind | null =
          link.type === "challenges" ? "challenge" : ["supports", "refines", "concedes_to"].includes(link.type) ? "support" : null;
        if (!kind) return;
        const { source_speaker: from, target_speaker: to, message_id } = link;
        if (from && to && from !== to && names[from] && names[to]) {
          beams.push({ key: `g${event.seq}-${i}`, from, to, kind });
        }
        if (message_id) {
          entries = entries.map((e) => (e.kind === "message" && e.id === message_id ? withTag(e, kind) : e));
        }
      });
      return { ...next, entries, beams: addBeams(next.beams, beams) };
    }

    case "notebook_updated":
      return {
        ...next,
        verdicts: { ...next.verdicts, [event.agent_id]: { verdict: event.verdict, confidence: event.confidence } },
      };

    case "alliance_changed": {
      const a = event.alliance;
      const who = nameOf(names, event.agent_id);
      const others = (ids: string[]) => list(names, ids.filter((id) => id !== event.agent_id));
      const reason = event.reason ? `: ${event.reason}` : "";
      const text = {
        proposed: `${who} offered ${list(names, event.targets)} an alliance${a.goal ? `: “${a.goal}”` : ""}`,
        accepted: `${who} joined the alliance with ${others(a.members)}`,
        refused: `${who} refused the alliance with ${others(a.members)}${reason}`,
        left: a.members.length ? `${who} left the alliance with ${others(a.members)}${reason}` : `${who} withdrew the alliance offer${reason}`,
        dissolved: "The alliance has ended",
      }[event.change];
      return { ...next, alliances: { ...next.alliances, [a.id]: a }, entries: notice(text, "alliance") };
    }

    case "poll_opened":
      return {
        ...next,
        polls: { ...next.polls, [event.poll.id]: { poll: event.poll, votes: {}, results: null } },
        activePollId: event.poll.id,
        entries: [...next.entries, { kind: "poll", id: `p${event.seq}`, pollId: event.poll.id }],
      };
    case "vote_cast": {
      const view = next.polls[event.poll_id];
      if (!view) return next;
      return {
        ...next,
        polls: { ...next.polls, [event.poll_id]: { ...view, votes: { ...view.votes, [event.voter_id]: event.option_id } } },
      };
    }
    case "poll_closed": {
      const view = next.polls[event.poll_id];
      if (!view) return next;
      return {
        ...next,
        polls: {
          ...next.polls,
          [event.poll_id]: { ...view, results: event.results, poll: { ...view.poll, status: "closed" } },
        },
      };
    }

    case "rule_violation":
      return { ...next, entries: notice(`Rule reminder for ${nameOf(names, event.agent_id)}: ${event.rule}`, "rule", event.agent_id) };
    case "engine_notice":
      return { ...next, entries: notice(event.message, "system") };
    case "waiting_for_moderator":
      return { ...next, waitingFor: { what: event.waiting_for, roundIndex: event.round_index } };
    case "error":
      return event.agent_id ? next : { ...next, entries: notice(event.message, "error") };
  }
}

/** Poll counts so far: from the results once closed, else from the votes cast. */
export function pollCounts(view: PollView): PollResult[] {
  if (view.results) return view.results;
  const total = Object.keys(view.votes).length;
  return view.poll.options.map((option) => {
    const voters = Object.entries(view.votes)
      .filter(([, chosen]) => chosen === option.id)
      .map(([voter]) => voter);
    return {
      option_id: option.id,
      label: option.label,
      votes: voters.length,
      percent: total ? Math.round((1000 * voters.length) / total) / 10 : 0,
      voters,
    };
  });
}

export const AUDIENCE_PREFIX = "Question for the audience:";

/** Split a conclusion into its statement and the question for the audience. */
export function splitConclusion(text: string): { statement: string; question: string | null } {
  const index = text.toLowerCase().lastIndexOf(AUDIENCE_PREFIX.toLowerCase());
  if (index < 0) return { statement: text.trim(), question: null };
  const question = text.slice(index + AUDIENCE_PREFIX.length).replace(/^[\s*_]+|[\s*_]+$/g, "");
  return { statement: text.slice(0, index).replace(/[\s*_]+$/g, "").trim(), question: question || null };
}
