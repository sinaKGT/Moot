// The alliance monitor: who backs whom, from the links between their points, next to the
// alliances they declared, as it was after any round. Computed by the server.

import { useEffect, useState } from "react";
import { api } from "../api";
import { Avatar } from "../avatar";
import { choiceLabel, type Names } from "../session/reducer";
import type { AllianceMonitor as Monitor, Persona, Side } from "../types";
import { formedRound, pairCounts, pairScore, pairStance, ringPositions, STANCE_LABEL, stepLine } from "./alliances";

const W = 380;
const H = 280;
const STANCE_COLOR = { backed: "#2dd4bf", opposed: "#f59e0b", mixed: "#94a3b8", none: "transparent" } as const;
const SIDE_LABEL: Record<Side, string> = { for: "for", against: "against", mixed: "mixed" };

interface Props {
  sessionId: string;
  agents: Persona[];
  names: Names;
  upToRound: number | null;
  version: number;
  allowAlliances: boolean;
  options: string[];
  choices: Record<string, string | null>;
}

export function AllianceMonitor({ sessionId, agents, names, upToRound, version, allowAlliances, options, choices }: Props) {
  const [monitor, setMonitor] = useState<Monitor | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    const timer = window.setTimeout(() => {
      api
        .alliances(sessionId, upToRound)
        .then((found) => {
          if (!current) return;
          setMonitor(found);
          setProblem(null);
        })
        .catch((e: Error) => current && setProblem(e.message));
    }, 250); // the graph often changes in bursts
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [sessionId, upToRound, version]);

  const name = (id: string) => names[id] ?? id;
  const color = Object.fromEntries(agents.map((a) => [a.id, a.color]));
  const seats = ringPositions(
    agents.map((a) => a.id),
    W,
    H,
  );
  const live = upToRound === null;
  const decision = options.length > 0;

  if (!monitor) {
    return (
      <section className="panel monitor" aria-labelledby="monitor-title">
        <h2 className="label" id="monitor-title">
          Alliances
        </h2>
        <p className={problem ? "problem" : "hint"}>{problem ?? "Loading…"}</p>
      </section>
    );
  }

  const pairs = [...monitor.pairs].sort((a, b) => b.support + b.challenge - (a.support + a.challenge));
  const invitations = monitor.alliances.flatMap((a) =>
    a.status === "dissolved" ? [] : a.invited.map((to) => ({ key: `${a.id}-${to}`, from: a.proposed_by, to })),
  );
  const flagged = new Set(monitor.mismatches.map((m) => [m.challenger, m.ally].sort().join("|")));
  const sideOf = (id: string) => {
    if (decision) return live && choices[id] ? choiceLabel(choices[id]!, options).toLowerCase() : null;
    const side = monitor.sides[id];
    return side ? SIDE_LABEL[side] : null;
  };

  const description = pairs
    .filter((p) => p.allied || pairStance(p) !== "none")
    .map((p) => `${name(p.first)} and ${name(p.second)}: ${p.allied ? "allied, " : ""}${STANCE_LABEL[pairStance(p)]}`)
    .join("; ");

  return (
    <section className="panel monitor" aria-labelledby="monitor-title">
      <h2 className="label" id="monitor-title">
        Alliances
      </h2>

      <svg className="network" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={description || "No exchanges between agents yet"}>
        {pairs.map((p) => {
          const a = seats[p.first];
          const b = seats[p.second];
          if (!a || !b) return null;
          const stance = pairStance(p);
          const width = 1.5 + Math.min(6, p.support + p.challenge) * 0.9;
          const key = [p.first, p.second].sort().join("|");
          return (
            <g key={key}>
              {p.allied && <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#2dd4bf" strokeOpacity={0.16} strokeWidth={18} strokeLinecap="round" />}
              {stance !== "none" && (
                <line
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={STANCE_COLOR[stance]}
                  strokeWidth={width}
                  strokeOpacity={0.35 + 0.6 * Math.abs(pairScore(p))}
                  strokeLinecap="round"
                />
              )}
              {flagged.has(key) && (
                <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 + 5} textAnchor="middle" className="network-flag">
                  !
                </text>
              )}
            </g>
          );
        })}
        {invitations.map((invite) => {
          const a = seats[invite.from];
          const b = seats[invite.to];
          return a && b ? <line key={invite.key} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#5eead4" strokeWidth={1.5} strokeDasharray="3 6" /> : null;
        })}
        {agents.map((agent) => {
          const seat = seats[agent.id];
          const side = sideOf(agent.id);
          return (
            <g key={agent.id} transform={`translate(${seat.x} ${seat.y})`}>
              <circle r={23} fill="#0b1220" stroke={agent.color} strokeOpacity={0.7} />
              <g transform="translate(-13 -13)">
                <Avatar name={agent.name} color={agent.color} size={26} />
              </g>
              <text y={39} textAnchor="middle" className="network-name">
                {agent.name}
              </text>
              {side && (
                <text y={53} textAnchor="middle" className="network-side">
                  {side}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      <ul className="pairs">
        {pairs.map((p) => {
          const stance = pairStance(p);
          return (
            <li key={`${p.first}-${p.second}`}>
              <span className="pair-names">
                <span style={{ color: color[p.first] }}>{name(p.first)}</span> – <span style={{ color: color[p.second] }}>{name(p.second)}</span>
                {p.allied && <span className="chip-mini allied">allied</span>}
              </span>
              <span className={`pair-stance ${stance}`}>
                {STANCE_LABEL[stance]}
                {stance !== "none" && <small> · {pairCounts(p)}</small>}
              </span>
            </li>
          );
        })}
      </ul>

      {live && monitor.mismatches.length > 0 && (
        <ul className="mismatches" aria-label="Allies at odds">
          {monitor.mismatches.map((m) => (
            <li key={`${m.alliance_id}-${m.challenger}-${m.ally}`}>
              <b>{name(m.challenger)}</b> has challenged ally {name(m.ally)} more than backed them since they allied (challenge{" "}
              {m.challenges}, support {m.supports}).
            </li>
          ))}
        </ul>
      )}

      <h3 className="label sub">Timeline</h3>
      {monitor.alliances.length === 0 ? (
        <p className="hint tight">{allowAlliances ? "No alliances yet. Agents can propose one when they agree." : "Alliances are off for this discussion."}</p>
      ) : (
        monitor.alliances.map((alliance) => {
          const formed = formedRound(alliance);
          return (
            <div key={alliance.id} className="alliance-story">
              <div className="alliance-head">
                <span className="goal">{alliance.goal ? `“${alliance.goal}”` : "Alliance"}</span>
                <span className={`chip-mini ${alliance.status}`}>
                  {alliance.status === "active" && formed !== null ? `active since round ${formed + 1}` : alliance.status}
                </span>
              </div>
              <ol className="timeline">
                {(alliance.history ?? []).map((step, i) => (
                  <li key={i}>
                    <span className="when">{step.round_index === null ? "–" : `R${step.round_index + 1}`}</span>
                    {stepLine(step, i === 0, alliance, name)}
                  </li>
                ))}
              </ol>
            </div>
          );
        })
      )}
      {problem && <p className="problem">Couldn't refresh: {problem}</p>}
    </section>
  );
}
