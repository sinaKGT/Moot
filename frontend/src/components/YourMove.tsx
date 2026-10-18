// When you moderate: what Moot is waiting for, with the obvious next steps as buttons.

import type { Action } from "../commands";
import type { DiscussionState } from "../session/reducer";
import type { Persona, Round } from "../types";

interface Props {
  waiting: NonNullable<DiscussionState["waitingFor"]>;
  rounds: Round[];
  agents: Persona[];
  onAction: (action: Action) => void;
}

export function YourMove({ waiting, rounds, agents, onAction }: Props) {
  if (waiting.what === "start_round") {
    const index = waiting.roundIndex ?? 0;
    return (
      <div className="your-move" role="status">
        <span>
          <strong>Your move.</strong>Start round {index + 1}: {rounds[index]?.name}, or speak first.
        </span>
        <button type="button" className="button primary small" onClick={() => onAction({ kind: "control", control: "next-round" })}>
          Start round {index + 1}
        </button>
      </div>
    );
  }
  if (waiting.what === "next_speaker") {
    return (
      <div className="your-move" role="status">
        <span>
          <strong>Who speaks next?</strong>
        </span>
        {agents.map((agent) => (
          <button key={agent.id} type="button" className="button small" style={{ color: agent.color }} onClick={() => onAction({ kind: "floor", agent: agent.name })}>
            {agent.name}
          </button>
        ))}
        <button type="button" className="button small" onClick={() => onAction({ kind: "floor", agent: null })}>
          Let Moot choose
        </button>
        <button type="button" className="button small" onClick={() => onAction({ kind: "control", control: "end-round" })}>
          End round
        </button>
      </div>
    );
  }
  return (
    <div className="your-move" role="status">
      <span>
        <strong>The round plan is done.</strong>Ask more, run a poll, or finish.
      </span>
      <button type="button" className="button small accent" onClick={() => onAction({ kind: "poll" })}>
        Run a poll
      </button>
      <button type="button" className="button primary small" onClick={() => onAction({ kind: "control", control: "finish" })}>
        Finish discussion
      </button>
    </div>
  );
}
