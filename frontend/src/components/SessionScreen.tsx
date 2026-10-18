// The discussion screen: The Chamber.

import { useCallback, useState } from "react";
import { api } from "../api";
import { parseInput, type Action } from "../commands";
import { useSession } from "../session/useSession";
import { Arena, Legend } from "./Arena";
import { Composer } from "./Composer";
import { Controls, Members, RoundPlan } from "./Rail";
import { Transcript } from "./Transcript";
import { YourMove } from "./YourMove";

const STATUS = {
  setup: { label: "READY", tone: "" },
  running: { label: "● LIVE", tone: "live" },
  paused: { label: "❚❚ PAUSED", tone: "paused" },
  finished: { label: "■ FINISHED", tone: "" },
} as const;

export function SessionScreen({ id, onLeave }: { id: string; onLeave: () => void }) {
  const { view, state, names, error } = useSession(id);
  const [problem, setProblem] = useState<string | null>(null);
  const [hiddenPoll, setHiddenPoll] = useState<string | null>(null);

  const run = useCallback(
    async (action: Action) => {
      switch (action.kind) {
        case "say":
          return void (await api.say(id, action.text));
        case "poll":
          return void (await api.poll(id, action.question, action.options));
        case "control":
          return void (await api.control(id, action.control));
        case "floor":
          return void (await api.floor(id, action.agent));
        case "remind":
          return void (await api.remind(id, action.agent, action.text));
        case "invalid":
          throw new Error(action.message);
      }
    },
    [id],
  );

  const act = useCallback(
    (action: Action) => {
      setProblem(null);
      run(action).catch((e: Error) => setProblem(e.message));
    },
    [run],
  );

  if (error || !view) {
    return (
      <div className="screen">
        <header className="topbar">
          <div className="brand">
            <span className="wordmark">MOOT</span>
          </div>
          <button type="button" className="button small" onClick={onLeave}>
            New discussion
          </button>
        </header>
        <div className="start">
          <p className={error ? "problem" : "hint"} role={error ? "alert" : "status"}>
            {error ? `This discussion couldn't be opened: ${error}` : "Opening…"}
          </p>
        </div>
      </div>
    );
  }

  const config = view.state.config;
  const agents = config.agents;
  const rounds = config.round_plan.rounds;
  const human = config.moderator.kind === "human";
  const moderatorChip = config.moderator.kind === "human" ? `${config.moderator.display_name} · ${config.moderator.role}`.toUpperCase() : "AI MODERATOR";
  const status = STATUS[state.status];
  const round = state.roundIndex !== null ? rounds[state.roundIndex] : null;

  return (
    <div className="screen">
      <header className="topbar">
        <div className="brand">
          <span className="wordmark">MOOT</span>
          <span className="divider" />
          <h1 className="topic">{config.topic}</h1>
        </div>
        <div className="chips">
          <span className={`chip ${status.tone}`}>{state.status === "finished" && state.endReason !== "completed" ? `■ ${state.endReason?.toUpperCase()}` : status.label}</span>
          {round && (
            <span className="chip">
              ROUND {(state.roundIndex ?? 0) + 1} OF {rounds.length} · {round.name.toUpperCase()}
            </span>
          )}
          <span className="chip">{moderatorChip}</span>
          <button type="button" className="button small" onClick={onLeave}>
            New discussion
          </button>
        </div>
      </header>

      <div className="body">
        <aside className="rail">
          <RoundPlan rounds={rounds} state={state} />
          <Members agents={agents} state={state} />
          <Controls state={state} human={human} onAction={act} />
        </aside>
        <main className="stage">
          <Arena
            agents={agents}
            rounds={rounds}
            topic={config.topic}
            moderatorName={view.summary.moderator}
            human={human}
            state={state}
            names={names}
            pollHidden={hiddenPoll === state.activePollId}
            onHidePoll={() => setHiddenPoll(state.activePollId)}
          />
          <Legend />
        </main>
        <section className="side" aria-label="Transcript">
          <h2 className="label">Transcript</h2>
          <Transcript state={state} agents={agents} rounds={rounds} names={names} human={human} />
        </section>
      </div>

      <div className="dock">
        {human && state.waitingFor && state.status !== "finished" && (
          <YourMove waiting={state.waitingFor} rounds={rounds} agents={agents} onAction={act} />
        )}
        {problem && (
          <p className="problem" role="alert">
            {problem}
          </p>
        )}
        <Composer
          agents={agents}
          human={human}
          disabled={state.status === "finished"}
          onSubmit={async (text) => {
            const action = parseInput(text, agents.map((a) => a.name));
            if (action.kind === "invalid") throw new Error(action.message);
            await run(action);
          }}
        />
      </div>
    </div>
  );
}
