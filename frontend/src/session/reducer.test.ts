import { describe, expect, it } from "vitest";
import type { MootEvent } from "../types";
import {
  choiceLabel,
  conclusionSections,
  initialState,
  MAX_BEAMS,
  pollCounts,
  reduce,
  splitConclusion,
  type DiscussionState,
  type MessageEntry,
} from "./reducer";

const names = { ava: "Ava", ben: "Ben", cleo: "Cleo", moderator: "Moderator", user: "You" };

type Draft = MootEvent extends infer E ? (E extends { seq: number } ? Omit<E, "seq"> : never) : never;

function play(drafts: Draft[], start: DiscussionState = initialState): DiscussionState {
  let seq = start.lastSeq;
  return drafts.reduce((state, draft) => reduce(state, { ...draft, seq: ++seq } as MootEvent, names), start);
}

const message = (id: string, speaker: string, text: string, extra: Partial<MessageEntry> & { mentions?: string[]; reply_to?: string | null } = {}) => ({
  type: "turn_completed" as const,
  message: {
    id,
    speaker_id: speaker,
    speaker_kind: (["moderator", "user", "system"].includes(speaker) ? speaker : "agent") as "agent" | "moderator" | "user" | "system",
    text,
    round_index: 0,
    mentions: extra.mentions ?? [],
    reply_to: extra.reply_to ?? null,
    usage: { prompt_tokens: 10, completion_tokens: 5 },
  },
});

const messages = (state: DiscussionState) => state.entries.filter((e): e is MessageEntry => e.kind === "message");

describe("the discussion reducer", () => {
  it("streams a turn and completes it", () => {
    const state = play([
      { type: "session_started", session_id: "s" },
      { type: "round_started", round_index: 0, round_id: "r" },
      { type: "turn_started", message_id: "m1", speaker_id: "ava", round_index: 0, reply_to: null, purpose: null },
      { type: "token_delta", message_id: "m1", text: "Yes, " },
      { type: "token_delta", message_id: "m1", text: "ban them" },
    ]);
    expect(state.status).toBe("running");
    expect(state.speakingId).toBe("ava");
    expect(messages(state)[0]).toMatchObject({ text: "Yes, ban them", done: false });

    const done = play([message("m1", "ava", "Yes, ban them.")], state);
    expect(messages(done)[0]).toMatchObject({ text: "Yes, ban them.", done: true, tokens: { in: 10, out: 5 } });
    expect(done.speakingId).toBeNull();
    expect(done.entries[0]).toMatchObject({ kind: "round", roundIndex: 0 });
  });

  it("ignores events it has already seen", () => {
    const state = play([{ type: "session_started", session_id: "s" }]);
    const again = reduce(state, { type: "session_paused", seq: 1 }, names);
    expect(again).toBe(state);
  });

  it("tags questions, answers, challenges and support, and draws beams", () => {
    let state = play([
      { type: "turn_started", message_id: "q", speaker_id: "cleo", round_index: 1, reply_to: null, purpose: null },
      message("q", "cleo", "@Ben would exemptions help?", { mentions: ["ben"] }),
      { type: "turn_started", message_id: "a", speaker_id: "ben", round_index: 1, reply_to: "q", purpose: null },
      message("a", "ben", "Partly.", { reply_to: "q" }),
    ]);
    state = play(
      [
        {
          type: "graph_updated",
          links: [
            { type: "challenges", source_speaker: "ben", target_speaker: "ava", message_id: "a" },
            { type: "supports", source_speaker: "cleo", target_speaker: "ava", message_id: "q" },
            { type: "answers", source_speaker: "ben", target_speaker: "cleo", message_id: "a" },
          ],
        },
      ],
      state,
    );
    const [question, answer] = messages(state);
    expect(question.tags).toEqual(["question", "support"]);
    expect(answer.tags).toEqual(["answer", "challenge"]);
    expect(state.beams.map((b) => [b.kind, b.from, b.to])).toEqual([
      ["question", "cleo", "ben"],
      ["challenge", "ben", "ava"],
      ["support", "cleo", "ava"],
    ]);
  });

  it("keeps only the most recent beams", () => {
    const drafts: Draft[] = Array.from({ length: MAX_BEAMS + 3 }, (_, i) =>
      message(`m${i}`, "ava", "@Ben?", { mentions: ["ben"] }),
    );
    expect(play(drafts).beams).toHaveLength(MAX_BEAMS);
  });

  it("turns system messages, decisions, alliances and errors into notices", () => {
    const state = play([
      message("s1", "system", "Ava could not answer."),
      { type: "moderator_decision", next_speaker_id: "ben", reason: "he was challenged" },
      {
        type: "alliance_changed",
        change: "proposed",
        agent_id: "ava",
        targets: ["cleo"],
        reason: "",
        alliance: { id: "al", goal: "Fair exemptions", proposed_by: "ava", members: ["ava"], invited: ["cleo"], status: "proposed" },
      },
      { type: "error", message: "The moderator could not speak", agent_id: null },
      { type: "error", message: "Ava failed", agent_id: "ava" },
    ]);
    const notices = state.entries.filter((e) => e.kind === "notice").map((e) => (e.kind === "notice" ? [e.tone, e.text] : []));
    expect(notices).toEqual([
      ["system", "Ava could not answer."],
      ["decision", "Moderator gives the floor to Ben: he was challenged"],
      ["alliance", "Ava offered Cleo an alliance: “Fair exemptions”"],
      ["error", "The moderator could not speak"],
    ]);
    expect(state.alliances.al.status).toBe("proposed");
    expect(state.entries[0].kind).toBe("notice");
  });

  it("follows a poll from opening to results", () => {
    const poll = {
      id: "p1",
      question: "Who made the strongest case?",
      kind: "winner" as const,
      options: [
        { id: "o_ava", label: "Ava", agent_id: "ava" },
        { id: "o_ben", label: "Ben", agent_id: "ben" },
      ],
      eligible_voters: ["ava", "ben", "cleo"],
      status: "open" as const,
      opened_by: "moderator",
      round_index: 3,
      previous_id: null,
    };
    let state = play([
      { type: "poll_opened", poll },
      { type: "vote_cast", poll_id: "p1", voter_id: "ben", option_id: "o_ava" },
      { type: "vote_cast", poll_id: "p1", voter_id: "cleo", option_id: "o_ava" },
    ]);
    expect(state.activePollId).toBe("p1");
    expect(pollCounts(state.polls.p1).map((r) => [r.label, r.votes, r.percent])).toEqual([
      ["Ava", 2, 100],
      ["Ben", 0, 0],
    ]);
    const results = [
      { option_id: "o_ava", label: "Ava", votes: 2, percent: 66.7, voters: ["ben", "cleo"] },
      { option_id: "o_ben", label: "Ben", votes: 1, percent: 33.3, voters: ["ava"] },
    ];
    state = play([{ type: "poll_closed", poll_id: "p1", results }], state);
    expect(state.polls.p1.poll.status).toBe("closed");
    expect(pollCounts(state.polls.p1)).toBe(results);
    state = play([{ type: "round_started", round_index: 4, round_id: "r5" }], state);
    expect(state.activePollId).toBeNull(); // a finished poll steps aside for the next round
  });

  it("knows what a human moderator is being asked, until they act", () => {
    let state = play([{ type: "waiting_for_moderator", waiting_for: "start_round", round_index: 0 }]);
    expect(state.waitingFor).toEqual({ what: "start_round", roundIndex: 0 });
    state = play([message("u1", "moderator", "Welcome!")], state);
    expect(state.waitingFor).not.toBeNull(); // talking first doesn't start the round
    state = play([{ type: "round_started", round_index: 0, round_id: "r" }], state);
    expect(state.waitingFor).toBeNull();
    state = play([{ type: "waiting_for_moderator", waiting_for: "next_speaker", round_index: 0 }], state);
    state = play([{ type: "moderator_decision", next_speaker_id: "ava", reason: "" }], state);
    expect(state.waitingFor).toBeNull();
  });

  it("records verdicts, the conclusion and the end", () => {
    const state = play([
      { type: "notebook_updated", agent_id: "ava", verdict: "yes", choice: null, confidence: 0.8 },
      { type: "turn_started", message_id: "c", speaker_id: "moderator", round_index: null, reply_to: null, purpose: "conclusion" },
      message("c", "moderator", "Undecided.\n\nQuestion for the audience: Would you?"),
      { type: "session_ended", reason: "completed" },
    ]);
    expect(state.verdicts.ava).toEqual({ verdict: "yes", choice: null, confidence: 0.8 });
    expect(state.conclusionId).toBe("c");
    expect(state).toMatchObject({ status: "finished", endReason: "completed" });
  });
});

describe("splitConclusion", () => {
  it("separates the question for the audience", () => {
    expect(splitConclusion("They agree.\n\nQuestion for the audience: Would you give up your car?")).toEqual({
      statement: "They agree.",
      question: "Would you give up your car?",
    });
    expect(splitConclusion("Done. **Question for the audience:** Who pays?")).toEqual({ statement: "Done.", question: "Who pays?" });
    expect(splitConclusion("No question here.")).toEqual({ statement: "No question here.", question: null });
  });
});

describe("a decision", () => {
  it("labels an agent's choice by the option's letter", () => {
    expect(choiceLabel(" berlin", ["Lisbon", "Berlin"])).toBe("OPTION B");
    expect(choiceLabel("a hybrid of both", ["Lisbon", "Berlin"])).toBe("A HYBRID OF BO");
  });
});

describe("a decision conclusion", () => {
  it("splits into its headings, with or without markdown, keeping list lines", () => {
    const text = [
      "After three rounds the panel converged.",
      "- **Decision:** Lisbon",
      "Why: lower costs and a strong talent pool; the vote went 2 to 1.",
      "Costs matter most.",
      "Risks: hiring takes longer than planned",
      "Revisit if: costs rise by a fifth",
      "## Next steps:",
      "- visit two offices",
      "- talk to a recruiter",
    ].join("\n");
    expect(conclusionSections(text)).toEqual([
      { heading: null, text: "After three rounds the panel converged." },
      { heading: "Decision", text: "Lisbon" },
      { heading: "Why", text: "lower costs and a strong talent pool; the vote went 2 to 1.\nCosts matter most." },
      { heading: "Risks", text: "hiring takes longer than planned" },
      { heading: "Revisit if", text: "costs rise by a fifth" },
      { heading: "Next steps", text: "- visit two offices\n- talk to a recruiter" },
    ]);
  });

  it("leaves an ordinary conclusion as one part without a heading", () => {
    expect(conclusionSections("The panel split.\nBen held firm.")).toEqual([{ heading: null, text: "The panel split.\nBen held firm." }]);
  });
});
