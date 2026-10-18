// The left side: the round plan, the panel with each agent's answer so far, and the controls.

import type { Action } from "../commands";
import { Avatar } from "../avatar";
import { choiceLabel, type DiscussionState } from "../session/reducer";
import type { Persona, Round } from "../types";

export function RoundPlan({ rounds, state }: { rounds: Round[]; state: DiscussionState }) {
  const finished = state.status === "finished";
  return (
    <section className="panel">
      <h2 className="label">Round plan</h2>
      <ol className="plan">
        {rounds.map((round, index) => {
          const current = !finished && state.roundIndex === index;
          const done = finished ? state.roundIndex !== null && index <= state.roundIndex : state.roundIndex !== null && index < state.roundIndex;
          return (
            <li key={round.id} className={current ? "current" : done ? "done" : ""} aria-current={current ? "step" : undefined}>
              <span className="dot" />
              <span>
                {index + 1} · {round.name}
                {current && (
                  <span className="meta">
                    {round.speaking_order === "moderator" ? "the moderator picks speakers" : round.blind ? "blind: nobody sees the others yet" : "in turn"}
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

const VERDICT_LABEL = { yes: "YES", no: "NO", unsure: "UNSURE" } as const;

const letter = (index: number) => String.fromCharCode(65 + index);
const fold = (text: string) => text.trim().toLowerCase();

/** In a decision: the options, and who leans towards each. */
export function Options({ options, agents, state }: { options: string[]; agents: Persona[]; state: DiscussionState }) {
  return (
    <section className="panel">
      <h2 className="label">Options</h2>
      <ol className="option-list">
        {options.map((option, index) => {
          const leaning = agents.filter((a) => {
            const choice = state.verdicts[a.id]?.choice;
            return choice && fold(choice) === fold(option);
          });
          return (
            <li key={option}>
              <span className="letter">{letter(index)}</span>
              <span className="text">{option}</span>
              <span className="leaning" aria-label={leaning.length ? `Leaning: ${leaning.map((a) => a.name).join(", ")}` : undefined}>
                {leaning.map((a) => (
                  <Avatar key={a.id} name={a.name} color={a.color} size={16} />
                ))}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function Members({ agents, state, options = [] }: { agents: Persona[]; state: DiscussionState; options?: string[] }) {
  return (
    <section className="panel">
      <h2 className="label">Panel</h2>
      <div className="members">
        {agents.map((agent) => {
          const verdict = state.verdicts[agent.id];
          return (
            <div key={agent.id} className="member">
              <Avatar name={agent.name} color={agent.color} />
              <div className="who">
                <div className="name">{agent.name}</div>
                <div className="role">
                  {agent.role}
                  {state.speakingId === agent.id ? " · speaking" : ""}
                </div>
              </div>
              {(verdict?.choice || verdict?.verdict) && (
                <span className="verdict" title={verdict.choice ?? undefined}>
                  {verdict.choice ? choiceLabel(verdict.choice, options) : VERDICT_LABEL[verdict.verdict!]}
                  {verdict.confidence !== null ? ` ${Math.round(verdict.confidence * 100)}%` : ""}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function Controls({ state, human, onAction }: { state: DiscussionState; human: boolean; onAction: (action: Action) => void }) {
  const over = state.status === "finished";
  const paused = state.status === "paused";
  return (
    <section className="controls" aria-label="Controls">
      <button type="button" className="button" disabled={over || state.status === "setup"} onClick={() => onAction({ kind: "control", control: paused ? "resume" : "pause" })}>
        {paused ? "Resume" : "Pause"}
      </button>
      <button type="button" className="button" disabled={over} onClick={() => onAction({ kind: "control", control: "next-round" })}>
        Next round
      </button>
      <button type="button" className="button accent" disabled={over} onClick={() => onAction({ kind: "poll" })}>
        Start poll
      </button>
      {human ? (
        <button type="button" className="button" disabled={over} onClick={() => onAction({ kind: "control", control: "end-round" })}>
          End round
        </button>
      ) : (
        <button type="button" className="button" disabled={over} onClick={() => onAction({ kind: "control", control: "finish" })}>
          Finish
        </button>
      )}
      {human && (
        <button type="button" className="button" disabled={over} onClick={() => onAction({ kind: "control", control: "finish" })}>
          Finish
        </button>
      )}
      <button type="button" className="button danger" disabled={over} onClick={() => onAction({ kind: "control", control: "stop" })}>
        Stop
      </button>
    </section>
  );
}
