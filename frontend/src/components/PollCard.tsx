// A poll over the round table: bars with counts and percentages, each voter's avatar on the
// option it chose, and how the same poll went last time.

import { Avatar } from "../avatar";
import { pollCounts, type Names, type PollView } from "../session/reducer";
import type { Persona } from "../types";

interface Props {
  view: PollView;
  previous?: PollView;
  names: Names;
  colors: Record<string, string>;
  agents: Persona[];
  onHide: () => void;
}

export function PollCard({ view, previous, names, colors, agents, onHide }: Props) {
  const counts = pollCounts(view);
  const voted = Object.keys(view.votes).length;
  const before = previous ? Object.fromEntries(pollCounts(previous).map((r) => [r.label, r.votes])) : null;
  const top = Math.max(0, ...counts.map((r) => r.votes));
  const byId = Object.fromEntries(agents.map((a) => [a.id, a]));
  const closed = view.poll.status === "closed";

  return (
    <section className="poll-card" aria-label={`Poll: ${view.poll.question}`} aria-live="polite">
      <button type="button" className="button small hide" onClick={onHide}>
        Hide
      </button>
      <div className="table-centre" style={{ position: "static", transform: "none", width: "auto", textAlign: "left" }}>
        <div className="kicker">Poll{view.poll.round_index !== null ? ` · round ${view.poll.round_index + 1}` : ""}</div>
      </div>
      <h2>{view.poll.question}</h2>
      <p className="sub">
        {voted} of {view.poll.eligible_voters.length} voted
        {view.poll.kind === "winner" ? " · nobody can vote for themselves" : ""}
        {closed ? "" : " · voting…"}
      </p>
      {counts.map((result) => {
        const option = view.poll.options.find((o) => o.id === result.option_id);
        const color = (option?.agent_id && colors[option.agent_id]) || "#2dd4bf";
        const leading = closed && top > 0 && result.votes === top;
        return (
          <div key={result.option_id} className="poll-row">
            <div className="line">
              <b style={{ color: option?.agent_id ? color : undefined }}>{result.label}</b>
              <span className="count">
                {result.votes} vote{result.votes === 1 ? "" : "s"} · {Math.round(result.percent)}%
              </span>
              {before && <span className="was">was {before[result.label] ?? 0}</span>}
            </div>
            <div className="bar">
              <div className="fill" style={{ width: `${result.percent}%`, background: color, boxShadow: leading ? `0 0 18px ${color}88` : undefined }} />
              {result.voters.length > 0 && (
                <div className="voters" style={{ left: `calc(${result.percent}% + 8px)` }} aria-label={`Chosen by ${result.voters.map((v) => names[v] ?? v).join(", ")}`}>
                  {result.voters.map((voter) =>
                    byId[voter] ? <Avatar key={voter} name={byId[voter].name} color={byId[voter].color} size={20} /> : null,
                  )}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </section>
  );
}
