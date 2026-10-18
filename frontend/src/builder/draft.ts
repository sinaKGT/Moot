// The team builder's draft: everything chosen before a discussion starts, as it is edited.
// Pure functions, so they can be tested without a browser.

import type { Library, ModelRef, Persona, Round, RoundType, SessionRules, SpeakingOrder, Traits } from "../types";

export const AGENT_COLORS = ["#60a5fa", "#fb7185", "#a3e635", "#f472b6", "#facc15", "#38bdf8", "#d6b98c"];
export const MAX_AGENTS = 12;
export const MAX_OPTIONS = 8;
export const DRAFT_VERSION = 1;

export const ROUND_TYPES: { type: RoundType; label: string; instruction: string }[] = [
  { type: "opening", label: "Opening statements", instruction: "Give your opening position on the topic." },
  { type: "challenge", label: "Challenge", instruction: "Challenge the strongest argument you disagree with, and name the participant who made it." },
  { type: "qa", label: "Questions", instruction: "Ask another participant a direct question using @Name, or answer questions put to you." },
  { type: "rebuttal", label: "Rebuttal", instruction: "Respond to the challenges made against your position." },
  { type: "free", label: "Free discussion", instruction: "Respond to whatever matters most in the discussion right now." },
  { type: "closing", label: "Closing statements", instruction: "Give your final position and say whether anything changed your mind." },
  { type: "vote", label: "Vote", instruction: "Vote in the poll." },
];

export interface DraftAgent extends Omit<Persona, "id"> {
  key: string;
}

export interface DraftRound extends Omit<Round, "id"> {
  key: string;
}

export type DraftModerator = { kind: "ai" } | { kind: "human"; display_name: string; role: string };

export interface Draft {
  version: number;
  topic: string;
  background: string;
  template: string;
  decision: boolean;
  options: string[];
  agents: DraftAgent[];
  selected: string | null;
  moderator: DraftModerator;
  defaultModel: ModelRef | null;
  rules: SessionRules;
  rounds: DraftRound[];
  allowAlliances: boolean;
}

let counter = 0;
export const newKey = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(counter++).toString(36)}`;

const fold = (text: string) => text.trim().toLowerCase();

export function uniqueName(name: string, taken: string[]): string {
  const base = name.trim() || "Agent";
  const used = new Set(taken.map(fold));
  if (!used.has(fold(base))) return base;
  for (let i = 2; ; i++) if (!used.has(fold(`${base} ${i}`))) return `${base} ${i}`;
}

export function nextColor(agents: DraftAgent[]): string {
  return AGENT_COLORS.find((c) => !agents.some((a) => a.color === c)) ?? AGENT_COLORS[agents.length % AGENT_COLORS.length];
}

const DEFAULT_TRAITS: Traits = { persuadability: 0.5, verbosity: 0.5, alliance_openness: 0.5 };

/** A library persona (or a blank one) as a new agent at the table, with a free name and colour. */
export function fromPersona(persona: Partial<Persona>, agents: DraftAgent[], keepColor = false): DraftAgent {
  const color = keepColor && persona.color && !agents.some((a) => a.color === persona.color) ? persona.color : nextColor(agents);
  return {
    key: newKey("agent"),
    name: uniqueName(persona.name ?? "Agent", agents.map((a) => a.name)),
    color,
    role: persona.role ?? "",
    viewpoint: persona.viewpoint ?? "",
    motivation: persona.motivation ?? "",
    expertise: persona.expertise ?? "",
    thinking_style: persona.thinking_style ?? "",
    traits: { ...DEFAULT_TRAITS, ...persona.traits },
    model: persona.model ?? null,
    prompt_override: persona.prompt_override ?? null,
  };
}

export function fromRounds(rounds: Round[]): DraftRound[] {
  return rounds.map((round) => ({
    key: newKey("round"),
    type: round.type,
    name: round.name,
    instruction: round.instruction,
    speaking_order: round.speaking_order,
    turns_per_agent: round.turns_per_agent,
    blind: round.blind,
    mentions_allowed: round.mentions_allowed,
    max_words: round.max_words ?? null,
  }));
}

export function newRound(type: RoundType): DraftRound {
  const info = ROUND_TYPES.find((t) => t.type === type)!;
  return {
    key: newKey("round"),
    type,
    name: info.label,
    instruction: info.instruction,
    speaking_order: "fixed",
    turns_per_agent: 1,
    blind: type === "opening",
    mentions_allowed: type === "qa" || type === "free",
    max_words: null,
  };
}

/** The ready-made panel: where the builder starts. */
export function initialDraft(library: Library): Draft {
  const agents: DraftAgent[] = [];
  for (const persona of library.example.agents) agents.push(fromPersona(persona, agents, true));
  return {
    version: DRAFT_VERSION,
    topic: library.example.topic,
    background: "",
    template: "example",
    decision: false,
    options: [],
    agents,
    selected: agents[0]?.key ?? null,
    moderator: { kind: "ai" },
    defaultModel: null,
    rules: { ...library.example.rules },
    rounds: fromRounds(library.example.round_plan.rounds),
    allowAlliances: true,
  };
}

export type DraftAction =
  | { type: "set"; patch: Partial<Pick<Draft, "topic" | "background" | "moderator" | "defaultModel" | "allowAlliances" | "decision">> }
  | { type: "addAgent"; persona: Partial<Persona> }
  | { type: "updateAgent"; key: string; patch: Partial<Omit<DraftAgent, "key">> }
  | { type: "removeAgent"; key: string }
  | { type: "selectAgent"; key: string | null }
  | { type: "applyTemplate"; template: string; rounds: Round[] }
  | { type: "addRound"; round: RoundType }
  | { type: "updateRound"; key: string; patch: Partial<Omit<DraftRound, "key">> }
  | { type: "removeRound"; key: string }
  | { type: "moveRound"; key: string; by: -1 | 1 }
  | { type: "addOption"; option: string }
  | { type: "removeOption"; index: number }
  | { type: "togglePreset"; preset: string }
  | { type: "addRule"; rule: string }
  | { type: "removeRule"; index: number }
  | { type: "setMaxWords"; words: number | null }
  | { type: "replace"; draft: Draft };

export function draftReducer(draft: Draft, action: DraftAction): Draft {
  switch (action.type) {
    case "set":
      return { ...draft, ...action.patch };
    case "addAgent": {
      if (draft.agents.length >= MAX_AGENTS) return draft;
      const agent = fromPersona(action.persona, draft.agents);
      return { ...draft, agents: [...draft.agents, agent], selected: agent.key };
    }
    case "updateAgent":
      return { ...draft, agents: draft.agents.map((a) => (a.key === action.key ? { ...a, ...action.patch } : a)) };
    case "removeAgent": {
      const agents = draft.agents.filter((a) => a.key !== action.key);
      return { ...draft, agents, selected: draft.selected === action.key ? (agents[0]?.key ?? null) : draft.selected };
    }
    case "selectAgent":
      return { ...draft, selected: action.key };
    case "applyTemplate":
      return {
        ...draft,
        template: action.template,
        rounds: fromRounds(action.rounds),
        decision: action.template === "decision" ? true : draft.decision,
      };
    case "addRound":
      return { ...draft, template: "custom", rounds: [...draft.rounds, newRound(action.round)] };
    case "updateRound":
      return { ...draft, template: "custom", rounds: draft.rounds.map((r) => (r.key === action.key ? { ...r, ...action.patch } : r)) };
    case "removeRound":
      return draft.rounds.length > 1 ? { ...draft, template: "custom", rounds: draft.rounds.filter((r) => r.key !== action.key) } : draft;
    case "moveRound": {
      const index = draft.rounds.findIndex((r) => r.key === action.key);
      const target = index + action.by;
      if (index < 0 || target < 0 || target >= draft.rounds.length) return draft;
      const rounds = [...draft.rounds];
      [rounds[index], rounds[target]] = [rounds[target], rounds[index]];
      return { ...draft, template: "custom", rounds };
    }
    case "addOption": {
      const option = action.option.replace(/\s+/g, " ").trim();
      if (!option || draft.options.length >= MAX_OPTIONS || draft.options.some((o) => fold(o) === fold(option))) return draft;
      return { ...draft, options: [...draft.options, option] };
    }
    case "removeOption":
      return { ...draft, options: draft.options.filter((_, i) => i !== action.index) };
    case "togglePreset": {
      const presets = draft.rules.presets.includes(action.preset)
        ? draft.rules.presets.filter((p) => p !== action.preset)
        : [...draft.rules.presets, action.preset];
      return { ...draft, rules: { ...draft.rules, presets } };
    }
    case "addRule": {
      const rule = action.rule.trim();
      return rule ? { ...draft, rules: { ...draft.rules, custom: [...draft.rules.custom, rule] } } : draft;
    }
    case "removeRule":
      return { ...draft, rules: { ...draft.rules, custom: draft.rules.custom.filter((_, i) => i !== action.index) } };
    case "setMaxWords":
      return { ...draft, rules: { ...draft.rules, max_words_per_turn: action.words } };
    case "replace":
      return action.draft;
  }
}

/** What stops the draft from starting, in plain words (the server checks again). */
export function problems(draft: Draft): string[] {
  const found: string[] = [];
  if (!draft.topic.trim()) found.push("Write a topic.");
  if (draft.agents.length < 2) found.push("Seat at least two agents.");
  const names = new Map<string, number>();
  for (const agent of draft.agents) {
    const name = agent.name.trim();
    if (!name) found.push("Every agent needs a name.");
    else if (/[@\n]/.test(name)) found.push(`${name}: names can't contain @.`);
    else if (name.length > 40) found.push(`${name.slice(0, 20)}…: keep names under 40 letters.`);
    names.set(fold(name), (names.get(fold(name)) ?? 0) + 1);
  }
  for (const [name, count] of names) if (name && count > 1) found.push(`Two agents are called ${name}.`);
  if (draft.moderator.kind === "human") {
    const name = draft.moderator.display_name.trim();
    if (!name) found.push("Write your name as moderator.");
    else if (names.has(fold(name))) found.push(`You and an agent are both called ${name}.`);
  }
  // the default model also runs the moderator and builds the argument map
  if (!draft.defaultModel) found.push("Choose a model.");
  if (draft.rounds.length === 0) found.push("Add at least one round.");
  const badLimit = (words: number | null) => words !== null && (!Number.isInteger(words) || words < 10 || words > 2000);
  if (badLimit(draft.rules.max_words_per_turn)) found.push("Max words per turn must be between 10 and 2000.");
  draft.rounds.forEach((round, index) => {
    if (badLimit(round.max_words)) found.push(`Round ${index + 1}: max words must be between 10 and 2000.`);
  });
  if (draft.decision && draft.options.length < 2) found.push("A decision needs at least two options.");
  return found;
}

const nonEmpty = (text: string) => text.trim();

/** The draft as the server's session configuration. */
export function toConfig(draft: Draft) {
  return {
    topic: draft.topic.trim(),
    background: draft.background.trim(),
    agents: draft.agents.map((a) => ({
      name: a.name.trim(),
      color: a.color,
      role: nonEmpty(a.role),
      viewpoint: nonEmpty(a.viewpoint),
      motivation: nonEmpty(a.motivation),
      expertise: nonEmpty(a.expertise),
      thinking_style: nonEmpty(a.thinking_style),
      traits: a.traits,
      model: a.model,
      prompt_override: a.prompt_override?.trim() || null,
    })),
    moderator:
      draft.moderator.kind === "human"
        ? { kind: "human", display_name: draft.moderator.display_name.trim(), role: draft.moderator.role.trim() || "Moderator" }
        : { kind: "ai" },
    rules: draft.rules,
    round_plan: {
      rounds: draft.rounds.map((r) => ({
        type: r.type,
        name: r.name.trim(),
        instruction: r.instruction.trim(),
        speaking_order: r.speaking_order as SpeakingOrder,
        turns_per_agent: r.turns_per_agent,
        blind: r.blind,
        mentions_allowed: r.mentions_allowed,
        max_words: r.max_words,
      })),
    },
    default_model: draft.defaultModel,
    allow_alliances: draft.allowAlliances,
    options: draft.decision ? draft.options : [],
  };
}

// ------------------------------------------------------------------ remembering the last draft

const STORAGE_KEY = "moot.draft";

export function loadDraft(): Draft | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const draft = raw ? (JSON.parse(raw) as Draft) : null;
    return draft && draft.version === DRAFT_VERSION && Array.isArray(draft.agents) ? draft : null;
  } catch {
    return null;
  }
}

export function saveDraft(draft: Draft): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
  } catch {
    // storage may be unavailable (private window): the draft just isn't remembered
  }
}
