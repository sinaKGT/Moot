// The argument map: every point made so far, by speaker (columns) and round (rows), with the
// links between them. Links to the topic and the moderator's sub-questions are badges.

import { useMemo, useState } from "react";
import { Avatar, ModeratorMark } from "../avatar";
import type { Names } from "../session/reducer";
import type { ArgumentGraph, EdgeType, GraphNode, Round } from "../types";
import { layoutMap, neighbours, ROW_PAD, type MapColumn } from "./mapLayout";

export const EDGE_STYLE: Record<EdgeType, { label: string; color: string; dash?: string; marker: string }> = {
  supports: { label: "supports", color: "#2dd4bf", marker: "teal" },
  refines: { label: "refines", color: "#2dd4bf", dash: "6 5", marker: "teal" },
  concedes_to: { label: "concedes to", color: "#2dd4bf", dash: "1 6", marker: "teal" },
  challenges: { label: "challenges", color: "#f59e0b", marker: "amber" },
  answers: { label: "answers", color: "#94a3b8", marker: "slate" },
};
const MARKERS = { teal: "#2dd4bf", amber: "#f59e0b", slate: "#94a3b8" };

const VERBS: Record<EdgeType, [string, string]> = {
  supports: ["Supports", "Supported by"],
  refines: ["Refines", "Refined by"],
  concedes_to: ["Concedes to", "Conceded to by"],
  challenges: ["Challenges", "Challenged by"],
  answers: ["Answers", "Answered by"],
};

const TYPE_LABEL: Record<GraphNode["type"], string> = {
  topic: "topic",
  subquestion: "sub-question",
  claim: "claim",
  evidence: "evidence",
  question: "question",
  concession: "concession",
};

interface Props {
  graph: ArgumentGraph;
  columns: MapColumn[];
  rounds: Round[];
  names: Names;
  upToRound: number | null;
  onShowMessage: (messageId: string) => void;
}

export function ArgumentMap({ graph, columns, rounds, names, upToRound, onShowMessage }: Props) {
  const layout = useMemo(() => layoutMap(graph, columns, upToRound), [graph, columns, upToRound]);
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const colors = Object.fromEntries(columns.map((c) => [c.id, c.color]));
  const placed = new Set(layout.nodes.map((p) => p.node.id));

  const focus = hovered ?? (selected && placed.has(selected) ? selected : null);
  const related = new Set<string>();
  if (focus) {
    related.add(focus);
    for (const { other } of neighbours(graph, focus)) related.add(other.id);
  }
  const subLabel = (index: number) => `Q${index + 1}`;
  const nameOf = (id: string | null) => (id ? (names[id] ?? id) : "");
  const roundLabel = (index: number | null) => (index === null ? "Before round 1" : `Round ${index + 1}`);

  return (
    <section className="panel map-panel" aria-labelledby="map-title">
      <div className="map-head">
        <h2 className="label" id="map-title">
          Argument map
        </h2>
        <MapLegend />
      </div>

      {layout.topic && (
        <div className="map-topic">
          <b>Topic</b> {layout.topic.text}
        </div>
      )}
      {layout.subquestions.length > 0 && (
        <ol className="map-subquestions" aria-label="The moderator's sub-questions">
          {layout.subquestions.map((q, i) => (
            <li key={q.id}>
              <span className="q">{subLabel(i)}</span> {q.text}
            </li>
          ))}
        </ol>
      )}

      {layout.nodes.length === 0 ? (
        <p className="hint map-empty">
          The map fills in as the agents speak: each point they make, and how points support, challenge or answer each other.
        </p>
      ) : (
        <div className="map-scroll">
          <div className="map-canvas" style={{ width: layout.width, height: layout.height }}>
            {layout.rows.map((row, i) => (
              <div key={row.roundIndex ?? "before"} className={`map-row${i % 2 ? " odd" : ""}`} style={{ top: row.y, height: row.h }}>
                <span className="map-row-label" style={{ top: ROW_PAD }}>
                  <b>{roundLabel(row.roundIndex)}</b>
                  {row.roundIndex !== null && rounds[row.roundIndex] && <small>{rounds[row.roundIndex].name}</small>}
                </span>
              </div>
            ))}
            {layout.columns.map((column) => (
              <div key={column.id} className="map-column-head" style={{ left: column.x }}>
                {column.id === "moderator" ? <ModeratorMark size={18} color={column.color} /> : <Avatar name={column.name} color={column.color} size={18} />}
                <span style={{ color: column.color }}>{column.name}</span>
              </div>
            ))}

            <svg className="map-lines" width={layout.width} height={layout.height} aria-hidden="true">
              <defs>
                {Object.entries(MARKERS).map(([key, color]) => (
                  <marker key={key} id={`map-arrow-${key}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M0,0 L10,5 L0,10 z" fill={color} />
                  </marker>
                ))}
              </defs>
              {layout.edges.map(({ edge, path }) => {
                const style = EDGE_STYLE[edge.type];
                const lit = !focus || edge.source === focus || edge.target === focus;
                return (
                  <path
                    key={edge.id}
                    d={path}
                    fill="none"
                    stroke={style.color}
                    strokeWidth={lit && focus ? 2.6 : 1.8}
                    strokeDasharray={style.dash}
                    strokeLinecap="round"
                    markerEnd={`url(#map-arrow-${style.marker})`}
                    opacity={lit ? 0.95 : 0.12}
                  />
                );
              })}
            </svg>

            {layout.nodes.map((p) => {
              const color = colors[p.column] ?? "#e2e8f0";
              const dim = focus !== null && !related.has(p.node.id);
              const to = p.node.addressed_to.map((id) => nameOf(id)).filter(Boolean);
              return (
                <button
                  key={p.node.id}
                  type="button"
                  className={`map-card${selected === p.node.id ? " selected" : ""}${dim ? " dim" : ""}${p.open ? " open" : ""}`}
                  style={{ left: p.x, top: p.y, width: p.w, height: p.h, ["--c" as string]: color }}
                  aria-pressed={selected === p.node.id}
                  onMouseEnter={() => setHovered(p.node.id)}
                  onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered(p.node.id)}
                  onBlur={() => setHovered(null)}
                  onClick={() => setSelected(selected === p.node.id ? null : p.node.id)}
                >
                  <span className="map-card-head">
                    <b style={{ color }}>{nameOf(p.node.speaker_id)}</b> · {TYPE_LABEL[p.node.type]}
                    {to.length > 0 && ` to ${to.join(", ")}`}
                  </span>
                  <span className="map-card-text">{p.node.text}</span>
                  <span className="map-badges">
                    {p.side && <span className={`badge ${p.side}`}>{p.side === "for" ? "for" : "against"}</span>}
                    {p.answers.map((i) => (
                      <span key={i} className="badge q" title={layout.subquestions[i]?.text}>
                        {subLabel(i)}
                      </span>
                    ))}
                    {p.open && <span className="badge open">unanswered</span>}
                    {p.node.sources.length > 0 && <span className="badge">{p.node.sources.length} source{p.node.sources.length === 1 ? "" : "s"}</span>}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      <PointDetail
        graph={graph}
        id={selected && placed.has(selected) ? selected : null}
        visible={(n) => placed.has(n.id) || n.type === "topic" || n.type === "subquestion"}
        names={names}
        colors={colors}
        subquestions={layout.subquestions}
        onSelect={setSelected}
        onShowMessage={onShowMessage}
      />
    </section>
  );
}

function MapLegend() {
  return (
    <div className="map-legend" aria-label="Line colours">
      {(["supports", "challenges", "answers", "refines", "concedes_to"] as EdgeType[]).map((type) => {
        const style = EDGE_STYLE[type];
        return (
          <span key={type}>
            <svg width="22" height="6" aria-hidden="true">
              <line x1="1" y1="3" x2="21" y2="3" stroke={style.color} strokeWidth="2" strokeDasharray={style.dash} strokeLinecap="round" />
            </svg>
            {style.label}
          </span>
        );
      })}
      <span>
        <i className="legend-open" aria-hidden="true" />
        unanswered
      </span>
    </div>
  );
}

interface DetailProps {
  graph: ArgumentGraph;
  id: string | null;
  visible: (node: GraphNode) => boolean;
  names: Names;
  colors: Record<string, string>;
  subquestions: GraphNode[];
  onSelect: (id: string) => void;
  onShowMessage: (messageId: string) => void;
}

function PointDetail({ graph, id, visible, names, colors, subquestions, onSelect, onShowMessage }: DetailProps) {
  if (!id) return <p className="hint map-detail-hint">Choose a point to see what it is linked to.</p>;
  const node = graph.nodes[id];
  const links = neighbours(graph, id).filter(({ other }) => visible(other)); // as of the chosen round
  const speaker = node.speaker_id ? (names[node.speaker_id] ?? node.speaker_id) : "";
  const describe = (other: GraphNode) => {
    if (other.type === "topic") return "the topic";
    if (other.type === "subquestion") return `Q${subquestions.findIndex((q) => q.id === other.id) + 1}`;
    return other.speaker_id ? (names[other.speaker_id] ?? other.speaker_id) : "";
  };
  return (
    <div className="map-detail" aria-live="polite">
      <div className="map-detail-head">
        <b style={{ color: (node.speaker_id && colors[node.speaker_id]) || undefined }}>{speaker}</b>
        <span>
          {TYPE_LABEL[node.type]} · {node.round_index === null ? "before round 1" : `round ${node.round_index + 1}`}
        </span>
        {node.message_id && (
          <button type="button" className="button small" onClick={() => onShowMessage(node.message_id!)}>
            Show in the discussion
          </button>
        )}
      </div>
      <p className="map-detail-text">{node.text}</p>
      {node.sources.length > 0 && (
        <ul className="map-sources">
          {node.sources.map((source) => (
            <li key={source}>{source}</li>
          ))}
        </ul>
      )}
      {links.length > 0 && (
        <ul className="map-links">
          {links.map(({ edge, other, outgoing }) => {
            const style = EDGE_STYLE[edge.type];
            const clickable = other.type !== "topic" && other.type !== "subquestion";
            const label = (
              <>
                <i style={{ background: style.color }} aria-hidden="true" />
                <span className="verb">
                  {VERBS[edge.type][outgoing ? 0 : 1]} {describe(other)}
                </span>
                <span className="quote">“{other.text}”</span>
              </>
            );
            return (
              <li key={edge.id}>
                {clickable ? (
                  <button type="button" onClick={() => onSelect(other.id)}>
                    {label}
                  </button>
                ) : (
                  <span className="static">{label}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
