// The second view of a discussion: the argument map and the alliance monitor, side by side,
// with one control to see both as they were after any round.

import { useMemo, useState } from "react";
import type { DiscussionState, Names } from "../session/reducer";
import type { SessionConfig } from "../types";
import { AllianceMonitor } from "./AllianceMonitor";
import { ArgumentMap } from "./ArgumentMap";
import type { MapColumn } from "./mapLayout";

interface Props {
  sessionId: string;
  config: SessionConfig;
  state: DiscussionState;
  names: Names;
  onShowMessage: (messageId: string) => void;
}

export function InsightsView({ sessionId, config, state, names, onShowMessage }: Props) {
  const rounds = config.round_plan.rounds;
  const started = state.roundIndex === null ? 0 : state.roundIndex + 1; // rounds begun so far
  const [after, setAfter] = useState<number | null>(null); // a round number, or null for now
  const shown = after !== null && after < started ? after : null;
  const upToRound = shown === null ? null : shown - 1;

  const columns = useMemo<MapColumn[]>(
    () => [
      ...config.agents.map((a) => ({ id: a.id, name: a.name, color: a.color })),
      { id: "moderator", name: names.moderator ?? "Moderator", color: config.moderator.kind === "human" ? "#fbbf24" : "#e2e8f0", optional: true },
      { id: "user", name: "You", color: "#5eead4", optional: true },
    ],
    [config, names.moderator],
  );
  const choices = Object.fromEntries(Object.entries(state.verdicts).map(([id, v]) => [id, v.choice]));

  return (
    <div className="insights">
      <div className="insights-time">
        {started === 0 ? (
          <span className="hint tight">Round 1 hasn't started yet.</span>
        ) : (
          <>
            <label htmlFor="as-after">{shown === null ? (state.status === "finished" ? "The whole discussion" : `Now · round ${started} of ${rounds.length}`) : `As it was after round ${shown} of ${rounds.length}`}</label>
            {started > 1 && (
              <input
                id="as-after"
                type="range"
                min={1}
                max={started}
                step={1}
                value={shown ?? started}
                onChange={(e) => {
                  const value = Number(e.target.value);
                  setAfter(value >= started ? null : value);
                }}
              />
            )}
            {shown !== null && (
              <button type="button" className="button small accent" onClick={() => setAfter(null)}>
                {state.status === "finished" ? "Show all" : "Back to now"}
              </button>
            )}
          </>
        )}
      </div>
      <div className="insights-body">
        <ArgumentMap graph={state.graph} columns={columns} rounds={rounds} names={names} upToRound={upToRound} onShowMessage={onShowMessage} />
        <AllianceMonitor
          sessionId={sessionId}
          agents={config.agents}
          names={names}
          upToRound={upToRound}
          version={state.insightVersion}
          allowAlliances={config.allow_alliances}
          options={config.options}
          choices={choices}
        />
      </div>
    </div>
  );
}
