// The round table: the moderator at the head, agents around it, the speaker glowing, and beams
// for challenges (amber), support (teal) and questions (violet). Alliances are dashed teal links.

import { Avatar, ModeratorMark } from "../avatar";
import { choiceLabel, type BeamKind, type DiscussionState, type Names } from "../session/reducer";
import type { Persona, Round } from "../types";
import { PollCard } from "./PollCard";

const SIZE = 520;
const CENTRE = SIZE / 2;
const RADIUS = 205;
const TABLE_CLEARANCE = 150; // beams pass at least this far from the centre, round the table
const BEAM_COLOR: Record<BeamKind, string> = { challenge: "#f59e0b", support: "#2dd4bf", question: "#a78bfa" };
// how far out each kind of line passes, so lines between the same two seats don't overlap
const LANE: Record<BeamKind | "alliance", number> = { question: 0, challenge: 14, support: -14, alliance: -24 };

interface Point {
  x: number;
  y: number;
}

export function seatPositions(agentIds: string[]): Record<string, Point> {
  const seats: Record<string, Point> = {};
  const all = ["moderator", ...agentIds];
  all.forEach((id, index) => {
    const angle = ((-90 + (index * 360) / all.length) * Math.PI) / 180;
    seats[id] = { x: CENTRE + RADIUS * Math.cos(angle), y: CENTRE + RADIUS * Math.sin(angle) };
  });
  return seats;
}

/** A curved path between two seats that stops short of their discs and never crosses the
 * table: its middle passes at least TABLE_CLEARANCE from the centre (plus a lane offset). */
export function beamPath(a: Point, b: Point, lane = 0): string {
  const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  const ux = (b.x - a.x) / length;
  const uy = (b.y - a.y) / length;
  const start = { x: a.x + ux * 44, y: a.y + uy * 44 };
  const end = { x: b.x - ux * 50, y: b.y - uy * 50 };
  const mid = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
  // the side of the line away from the centre (left of travel when it runs straight across)
  const normal = { x: -uy, y: ux };
  let side = (mid.x - CENTRE) * normal.x + (mid.y - CENTRE) * normal.y;
  if (Math.abs(side) < 1) side = 1;
  const sign = Math.sign(side);
  const reach = Math.max(Math.abs(side) * 0.85, TABLE_CLEARANCE) + lane;
  // a quadratic curve's middle lies halfway between the chord's middle and its control point
  const push = 2 * (reach - Math.abs(side)) * sign;
  const ctrl = { x: mid.x + normal.x * push, y: mid.y + normal.y * push };
  const r = (n: number) => Math.round(n * 10) / 10;
  return `M${r(start.x)},${r(start.y)} Q${r(ctrl.x)},${r(ctrl.y)} ${r(end.x)},${r(end.y)}`;
}

const pct = (n: number) => `${(n / SIZE) * 100}%`;

interface Props {
  agents: Persona[];
  rounds: Round[];
  topic: string;
  moderatorName: string;
  human: boolean;
  state: DiscussionState;
  names: Names;
  pollHidden: boolean;
  onHidePoll: () => void;
  options?: string[];
}

export function Arena({ agents, rounds, topic, moderatorName, human, state, names, pollHidden, onHidePoll, options = [] }: Props) {
  const seats = seatPositions(agents.map((a) => a.id));
  const colors = Object.fromEntries(agents.map((a) => [a.id, a.color]));
  const round = state.roundIndex !== null ? rounds[state.roundIndex] : null;
  const speaking = state.speakingId;
  const allianceLinks = Object.values(state.alliances)
    .filter((a) => a.status !== "dissolved")
    .flatMap((a) =>
      a.status === "active"
        ? a.members.flatMap((m, i) => a.members.slice(i + 1).map((n) => ({ key: `${a.id}-${m}-${n}`, from: m, to: n, offer: false })))
        : a.invited.map((n) => ({ key: `${a.id}-${n}`, from: a.proposed_by, to: n, offer: true })),
    );
  const stateLine =
    state.status === "finished"
      ? "Finished"
      : state.status === "paused"
        ? "Paused"
        : state.waitingFor && human
          ? "Your move"
          : speaking
            ? `${names[speaking] ?? ""} is speaking`
            : state.status === "running"
              ? "Thinking…"
              : "Ready";
  const description = `Round table. ${stateLine}.`;
  const poll = state.activePollId && !pollHidden ? state.polls[state.activePollId] : null;
  const previous = poll?.poll.previous_id ? state.polls[poll.poll.previous_id] : undefined;

  return (
    <div className="arena" role="img" aria-label={description}>
      <svg className="lines" viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden="true">
        <defs>
          {(Object.keys(BEAM_COLOR) as BeamKind[]).map((kind) => (
            <marker key={kind} id={`moot-head-${kind}`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill={BEAM_COLOR[kind]} />
            </marker>
          ))}
        </defs>
        <circle cx={CENTRE} cy={CENTRE} r={RADIUS + 31} fill="none" stroke="rgba(148,163,184,0.18)" />
        <circle cx={CENTRE} cy={CENTRE} r={RADIUS - 5} fill="none" stroke="rgba(148,163,184,0.08)" strokeDasharray="2 6" />
        <circle cx={CENTRE} cy={CENTRE} r={118} fill="rgba(15,23,36,0.85)" stroke="rgba(45,212,191,0.35)" strokeWidth={1.5} />
        {allianceLinks.map((link) =>
          seats[link.from] && seats[link.to] ? (
            <path key={link.key} d={beamPath(seats[link.from], seats[link.to], LANE.alliance)} fill="none" stroke="#2dd4bf" strokeWidth={link.offer ? 1.5 : 2.5} strokeDasharray="6 6" opacity={link.offer ? 0.55 : 0.75} />
          ) : null,
        )}
        {state.beams.map((beam, index) =>
          seats[beam.from] && seats[beam.to] ? (
            <path
              key={beam.key}
              d={beamPath(seats[beam.from], seats[beam.to], LANE[beam.kind])}
              fill="none"
              stroke={BEAM_COLOR[beam.kind]}
              strokeWidth={beam.kind === "challenge" ? 2.5 : 2}
              strokeDasharray={beam.kind === "question" ? "1 7" : undefined}
              strokeLinecap="round"
              markerEnd={`url(#moot-head-${beam.kind})`}
              opacity={index >= state.beams.length - 2 ? 1 : 0.35}
            />
          ) : null,
        )}
      </svg>

      <div className="table-centre">
        <div className="kicker">{round ? `Round ${(state.roundIndex ?? 0) + 1} · ${round.name}` : state.status === "finished" ? "Finished" : "Opening"}</div>
        <div className="question">{topic}</div>
        <div className="state">{stateLine}</div>
      </div>

      <div className={`seat${speaking === "moderator" ? " speaking" : ""}`} style={{ left: pct(seats.moderator.x), top: pct(seats.moderator.y) }}>
        <div className="disc" style={{ borderColor: "rgba(226,232,240,0.5)", boxShadow: speaking === "moderator" ? "0 0 0 3px #e2e8f0, 0 0 30px rgba(226,232,240,0.45)" : undefined }}>
          <ModeratorMark size={32} />
        </div>
        <div className="seat-name">{moderatorName}</div>
      </div>
      {agents.map((agent) => {
        const seat = seats[agent.id];
        const isSpeaking = speaking === agent.id;
        const notes = state.verdicts[agent.id];
        const leaning = notes?.choice ? choiceLabel(notes.choice, options) : notes?.verdict;
        return (
          <div key={agent.id} className={`seat${isSpeaking ? " speaking" : ""}`} style={{ left: pct(seat.x), top: pct(seat.y) }}>
            <div className="disc" style={{ borderColor: `${agent.color}99`, boxShadow: isSpeaking ? `0 0 0 3px ${agent.color}, 0 0 34px ${agent.color}99` : undefined }}>
              <Avatar name={agent.name} color={agent.color} size={34} />
            </div>
            <div className="seat-name">{agent.name}</div>
            <div className="seat-note" style={isSpeaking ? { color: agent.color } : undefined}>
              {isSpeaking ? "speaking" : leaning ?? ""}
            </div>
          </div>
        );
      })}

      {poll && <PollCard view={poll} previous={previous} names={names} colors={colors} agents={agents} onHide={onHidePoll} />}
    </div>
  );
}

export function Legend() {
  return (
    <div className="legend" aria-hidden="true">
      <span>
        <i style={{ borderColor: "#2dd4bf" }} />
        support
      </span>
      <span>
        <i style={{ borderColor: "#f59e0b", borderTopWidth: 3 }} />
        challenge
      </span>
      <span>
        <i style={{ borderColor: "#a78bfa", borderTopStyle: "dotted" }} />
        question
      </span>
      <span>
        <i style={{ borderColor: "#2dd4bf", borderTopStyle: "dashed" }} />
        alliance
      </span>
    </div>
  );
}
