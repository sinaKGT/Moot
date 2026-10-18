import { describe, expect, it } from "vitest";
import { describeProblem } from "../api";
import type { Library, Persona, Round, SessionConfig } from "../types";
import { applyProposal, draftFromConfig, draftReducer, initialDraft, MAX_OPTIONS, newRound, problems, toConfig, uniqueName, type Draft } from "./draft";

const persona = (name: string, color: string): Persona => ({
  id: `agt_${name}`,
  name,
  color,
  role: "Role",
  viewpoint: `${name}'s view`,
  motivation: "",
  expertise: "",
  thinking_style: "",
  traits: { persuadability: 0.3, verbosity: 0.4, alliance_openness: 0.7 },
  model: null,
  prompt_override: null,
});

const round = (type: Round["type"], name: string): Round => ({
  id: `rnd_${name}`,
  type,
  name,
  instruction: "",
  speaking_order: "fixed",
  turns_per_agent: 1,
  blind: false,
  mentions_allowed: false,
  max_words: null,
});

const library: Library = {
  personas: [persona("Skeptic", "#facc15")],
  templates: {
    decision: { name: "Decision", plan: { rounds: [round("opening", "Options"), round("vote", "Decide")] } },
  },
  rule_presets: { answer_first: "Answer first." },
  global_rules: [],
  example: {
    topic: "Cars?",
    agents: [persona("Ava", "#60a5fa"), persona("Ben", "#fb7185"), persona("Cleo", "#a3e635")],
    round_plan: { rounds: [round("opening", "Opening"), round("vote", "Vote")] },
    rules: { presets: ["answer_first"], custom: [], max_words_per_turn: 120 },
  },
};

const ready = (): Draft => ({
  ...initialDraft(library),
  topic: "Should we?",
  defaultModel: { provider: "openrouter", model: "m" },
});

describe("the team builder's draft", () => {
  it("starts from the example panel with its colours, rounds and rules", () => {
    const draft = initialDraft(library);
    expect(draft.agents.map((a) => [a.name, a.color])).toEqual([
      ["Ava", "#60a5fa"],
      ["Ben", "#fb7185"],
      ["Cleo", "#a3e635"],
    ]);
    expect(draft.rounds.map((r) => r.name)).toEqual(["Opening", "Vote"]);
    expect(draft.rules.max_words_per_turn).toBe(120);
    expect(draft.topic).toBe("Cars?");
    expect(draft.selected).toBe(draft.agents[0].key);
  });

  it("gives a new agent a free name and colour, and selects it", () => {
    let draft = ready();
    draft = draftReducer(draft, { type: "addAgent", persona: { name: "Ava" } });
    const added = draft.agents[3];
    expect(added.name).toBe("Ava 2");
    expect(added.color).toBe("#f472b6");
    expect(draft.selected).toBe(added.key);
    expect(uniqueName("ava", ["Ava", "Ava 2"])).toBe("ava 3");
  });

  it("selects another agent when the selected one leaves", () => {
    let draft = ready();
    const first = draft.agents[0].key;
    draft = draftReducer(draft, { type: "removeAgent", key: first });
    expect(draft.agents).toHaveLength(2);
    expect(draft.selected).toBe(draft.agents[0].key);
  });

  it("turns decision mode on with the Decision format, and marks edited plans as custom", () => {
    let draft = draftReducer(ready(), { type: "applyTemplate", template: "decision", rounds: library.templates.decision.plan.rounds });
    expect(draft.decision).toBe(true);
    expect(draft.template).toBe("decision");
    draft = draftReducer(draft, { type: "addRound", round: "closing" });
    expect(draft.template).toBe("custom");
    expect(draft.rounds.at(-1)).toMatchObject({ name: "Closing statements", type: "closing" });
  });

  it("moves rounds within the plan and keeps at least one", () => {
    let draft = ready();
    const [opening, vote] = draft.rounds;
    draft = draftReducer(draft, { type: "moveRound", key: vote.key, by: -1 });
    expect(draft.rounds.map((r) => r.key)).toEqual([vote.key, opening.key]);
    expect(draftReducer(draft, { type: "moveRound", key: vote.key, by: -1 })).toBe(draft);
    draft = draftReducer(draft, { type: "removeRound", key: vote.key });
    expect(draftReducer(draft, { type: "removeRound", key: opening.key }).rounds).toHaveLength(1);
  });

  it("tidies options, skips repeats and stops at the limit", () => {
    let draft = ready();
    for (const option of ["  Berlin ", "berlin", "", "Lisbon"]) draft = draftReducer(draft, { type: "addOption", option });
    expect(draft.options).toEqual(["Berlin", "Lisbon"]);
    for (let i = 0; i < 10; i++) draft = draftReducer(draft, { type: "addOption", option: `City ${i}` });
    expect(draft.options).toHaveLength(MAX_OPTIONS);
  });

  it("explains what stops a draft from starting", () => {
    expect(problems(ready())).toEqual([]);
    const draft: Draft = {
      ...ready(),
      topic: " ",
      defaultModel: null,
      decision: true,
      options: ["Only one"],
      moderator: { kind: "human", display_name: "ben", role: "Judge" },
    };
    expect(problems(draft)).toEqual([
      "Write a topic.",
      "You and an agent are both called ben.",
      "Choose a model.",
      "A decision needs at least two options.",
    ]);
    const twins = draftReducer(ready(), { type: "updateAgent", key: ready().agents[0].key, patch: {} });
    twins.agents[1] = { ...twins.agents[1], name: "ava" };
    expect(problems(twins)).toContain("Two agents are called ava.");
  });

  it("builds the server's configuration without the builder's own fields", () => {
    let draft = ready();
    draft = draftReducer(draft, { type: "addOption", option: "Berlin" });
    draft = draftReducer(draft, { type: "updateAgent", key: draft.agents[0].key, patch: { prompt_override: "  " } });
    const config = toConfig(draft);
    expect(config.options).toEqual([]); // options only count in a decision
    expect(config.agents[0]).not.toHaveProperty("key");
    expect(config.agents[0].prompt_override).toBeNull();
    expect(config.moderator).toEqual({ kind: "ai" });
    expect(config.agents[0].knowledge).toBeNull();
    const withFiles = draftReducer(draft, { type: "updateAgent", key: draft.agents[1].key, patch: { knowledge: "kb_1" } });
    expect(toConfig(withFiles).agents[1].knowledge).toBe("kb_1");
    const withTools = draftReducer(draft, { type: "updateAgent", key: draft.agents[0].key, patch: { tools: ["calculator"] } });
    expect(toConfig(withTools).agents.map((a) => a.tools)).toEqual([["calculator"], [], []]);
    expect(config.round_plan.rounds[0]).not.toHaveProperty("key");
    const decided = toConfig({ ...draft, decision: true, options: ["Berlin", "Lisbon"] });
    expect(decided.options).toEqual(["Berlin", "Lisbon"]);
  });

  it("takes an earlier discussion's setup back into the builder unchanged", () => {
    let draft = ready();
    draft = draftReducer(draft, { type: "set", patch: { decision: true, moderator: { kind: "human", display_name: "Sina", role: "Judge" } } });
    draft = draftReducer(draft, { type: "addOption", option: "Lisbon" });
    draft = draftReducer(draft, { type: "addOption", option: "Berlin" });
    const config = toConfig(draft);
    const back = draftFromConfig({
      ...config,
      agents: config.agents.map((a, i) => ({ ...a, id: `agt_${i}` })),
      moderator: config.moderator as SessionConfig["moderator"],
      round_plan: { rounds: config.round_plan.rounds.map((r, i) => ({ ...r, id: `rnd_${i}` })) },
    });
    expect(toConfig(back)).toEqual(config);
    expect(back.decision).toBe(true);
    expect(back.template).toBe("custom");
  });

  it("makes a new round with the default name and instruction", () => {
    expect(newRound("challenge")).toMatchObject({
      name: "Challenge",
      instruction: "Challenge the strongest argument you disagree with, and name the participant who made it.",
      blind: false,
    });
    expect(newRound("opening").blind).toBe(true);
  });
});

describe("the Team Designer's proposal", () => {
  it("replaces the topic, panel, format and options, and keeps the rest", () => {
    const before: Draft = { ...ready(), background: "Budget: 2m", moderator: { kind: "human", display_name: "Sina", role: "Judge" } };
    const proposal = {
      topic: "Which city for our next office?",
      format: "decision",
      options: ["Lisbon", "Berlin"],
      agents: [persona("Ines", "#000000"), persona("Jonas", "#000000"), persona("Mira", "#000000")],
      rationale: "Each option has a champion.",
      balance_notes: [],
      revised: false,
    };
    const after = applyProposal(before, proposal, library);
    expect(after.topic).toBe("Which city for our next office?");
    expect(after.agents.map((a) => [a.name, a.color])).toEqual([
      ["Ines", "#60a5fa"],
      ["Jonas", "#fb7185"],
      ["Mira", "#a3e635"],
    ]);
    expect(after.selected).toBe(after.agents[0].key);
    expect(after.template).toBe("decision");
    expect(after.rounds.map((r) => r.name)).toEqual(["Options", "Decide"]);
    expect([after.decision, after.options]).toEqual([true, ["Lisbon", "Berlin"]]);
    expect(after.background).toBe("Budget: 2m");
    expect(after.moderator).toEqual(before.moderator);
    expect(after.defaultModel).toEqual(before.defaultModel);
    expect(problems(after)).toEqual([]);

    const debate = applyProposal(after, { ...proposal, format: "unknown", options: [] }, library);
    expect([debate.template, debate.decision, debate.options]).toEqual(["decision", false, []]); // keeps the plan it had
  });
});

describe("server validation messages", () => {
  it("names the agent or round and drops pydantic's prefix", () => {
    expect(describeProblem({ loc: ["body", "agents", 1, "name"], msg: "Value error, too long" })).toBe("Agent 2 · name: too long");
    expect(describeProblem({ loc: ["body", "round_plan", "rounds", 0, "max_words"], msg: "too small" })).toBe(
      "Round 1 · max words: too small",
    );
    expect(describeProblem({ loc: ["body"], msg: "Value error, a decision needs at least two options" })).toBe(
      "a decision needs at least two options",
    );
  });
});
