// Lays out the argument map: one column per speaker, one row per round, so you can read who
// said what over time and see the links between points cross from column to column.
// Pure and deterministic (cards have a fixed size), so it is tested without a browser.

import type { ArgumentGraph, GraphEdge, GraphNode } from "../types";

export const CARD_W = 200;
export const CARD_H = 114;
export const COL_GAP = 64;
export const STACK_GAP = 14;
export const ROW_PAD = 18;
export const LABEL_W = 92; // round labels on the left
export const HEAD_H = 44; // speaker names on top
const PORT_GAP = 11;

export interface MapColumn {
  id: string;
  name: string;
  color: string;
  /** Shown only when it has points (the moderator, you). Agents always have a column. */
  optional?: boolean;
}

export interface PlacedNode {
  node: GraphNode;
  column: string;
  x: number;
  y: number;
  w: number;
  h: number;
  /** Its stance on the topic, from links to the topic (drawn as a badge, not a line). */
  side: "for" | "against" | null;
  /** The moderator's sub-questions it takes up, by position (Q1 is 0). */
  answers: number[];
  /** A question nobody has answered yet. */
  open: boolean;
}

export interface PlacedEdge {
  edge: GraphEdge;
  path: string;
  mid: { x: number; y: number };
}

export interface MapRow {
  roundIndex: number | null;
  y: number;
  h: number;
}

export interface MapLayout {
  width: number;
  height: number;
  columns: (MapColumn & { x: number })[];
  rows: MapRow[];
  nodes: PlacedNode[];
  edges: PlacedEdge[];
  topic: GraphNode | null;
  subquestions: GraphNode[];
}

type Side = "left" | "right";
const rowKey = (round: number | null) => (round === null ? -1 : round);

/** The map as it was after `upToRound` (null: everything so far). */
export function layoutMap(graph: ArgumentGraph, columns: MapColumn[], upToRound: number | null = null): MapLayout {
  const all = Object.values(graph.nodes);
  const topic = all.find((n) => n.type === "topic") ?? null;
  const subquestions = all.filter((n) => n.type === "subquestion");
  const known = new Set(columns.map((c) => c.id));
  const shown = (n: GraphNode) => upToRound === null || n.round_index === null || n.round_index <= upToRound;
  const points = all.filter(
    (n) => n.type !== "topic" && n.type !== "subquestion" && n.speaker_id !== null && known.has(n.speaker_id) && shown(n),
  );

  const used = columns.filter((c) => !c.optional || points.some((p) => p.speaker_id === c.id));
  const placedColumns = used.map((c, i) => ({ ...c, x: LABEL_W + i * (CARD_W + COL_GAP) + COL_GAP / 2 }));
  const columnX = Object.fromEntries(placedColumns.map((c) => [c.id, c.x]));

  // rows: one per round with points, the time before round 1 first
  const rounds = [...new Set(points.map((p) => rowKey(p.round_index)))].sort((a, b) => a - b);
  const rows: MapRow[] = [];
  const nodes: PlacedNode[] = [];
  let y = HEAD_H;
  for (const key of rounds) {
    const inRow = points.filter((p) => rowKey(p.round_index) === key);
    const stack = Math.max(1, ...used.map((c) => inRow.filter((p) => p.speaker_id === c.id).length));
    const h = ROW_PAD * 2 + stack * CARD_H + (stack - 1) * STACK_GAP;
    rows.push({ roundIndex: key === -1 ? null : key, y, h });
    for (const column of used) {
      inRow
        .filter((p) => p.speaker_id === column.id)
        .forEach((node, i) => {
          nodes.push({
            node,
            column: column.id,
            x: columnX[column.id],
            y: y + ROW_PAD + i * (CARD_H + STACK_GAP),
            w: CARD_W,
            h: CARD_H,
            side: null,
            answers: [],
            open: false,
          });
        });
    }
    y += h;
  }

  const byId = new Map(nodes.map((p) => [p.node.id, p]));
  const subIndex = new Map(subquestions.map((n, i) => [n.id, i]));
  const drawn: GraphEdge[] = [];
  const stance = new Map<string, [number, number]>();
  for (const edge of graph.edges) {
    const source = byId.get(edge.source);
    if (!source) continue;
    if (topic && edge.target === topic.id) {
      const [pro, contra] = stance.get(edge.source) ?? [0, 0];
      if (edge.type === "supports") stance.set(edge.source, [pro + 1, contra]);
      if (edge.type === "challenges") stance.set(edge.source, [pro, contra + 1]);
    } else if (subIndex.has(edge.target)) {
      const index = subIndex.get(edge.target)!;
      if (!source.answers.includes(index)) source.answers.push(index);
    } else if (byId.has(edge.target)) {
      drawn.push(edge);
    }
  }
  for (const [id, [pro, contra]] of stance) {
    byId.get(id)!.side = pro > contra ? "for" : contra > pro ? "against" : null;
  }
  for (const placed of nodes) {
    placed.answers.sort((a, b) => a - b);
    placed.open = placed.node.type === "question" && !drawn.some((e) => e.type === "answers" && e.target === placed.node.id);
  }

  return {
    width: LABEL_W + used.length * (CARD_W + COL_GAP),
    height: y + ROW_PAD,
    columns: placedColumns,
    rows,
    nodes,
    edges: routeEdges(drawn, byId),
    topic,
    subquestions,
  };
}

/** Which sides of the two cards a link leaves and enters: the facing sides, or the left side
 * of both when they share a column (the curve then bulges out to the left). */
function sidesOf(a: PlacedNode, b: PlacedNode): [Side, Side] {
  if (a.x === b.x) return ["left", "left"];
  return b.x < a.x ? ["left", "right"] : ["right", "left"];
}

function routeEdges(edges: GraphEdge[], byId: Map<string, PlacedNode>): PlacedEdge[] {
  // spread the links that meet one side of a card, ordered by where their other end is
  const ports = new Map<string, { edge: string; otherY: number }[]>();
  const add = (node: PlacedNode, side: Side, edge: string, other: PlacedNode) => {
    const key = `${node.node.id}:${side}`;
    ports.set(key, [...(ports.get(key) ?? []), { edge, otherY: other.y + other.h / 2 }]);
  };
  for (const e of edges) {
    const a = byId.get(e.source)!;
    const b = byId.get(e.target)!;
    const [sa, sb] = sidesOf(a, b);
    add(a, sa, e.id, b);
    add(b, sb, e.id, a);
  }
  const offset = (node: PlacedNode, side: Side, edge: string) => {
    const list = [...(ports.get(`${node.node.id}:${side}`) ?? [])].sort((p, q) => p.otherY - q.otherY);
    const i = list.findIndex((p) => p.edge === edge);
    const limit = node.h / 2 - 10;
    return Math.max(-limit, Math.min(limit, (i - (list.length - 1) / 2) * PORT_GAP));
  };
  const point = (node: PlacedNode, side: Side, edge: string) => ({
    x: side === "left" ? node.x : node.x + node.w,
    y: node.y + node.h / 2 + offset(node, side, edge),
  });
  const r = (n: number) => Math.round(n * 10) / 10;

  return edges.map((edge) => {
    const a = byId.get(edge.source)!;
    const b = byId.get(edge.target)!;
    const [sa, sb] = sidesOf(a, b);
    const start = point(a, sa, edge.id);
    const end = point(b, sb, edge.id);
    let c1: { x: number; y: number };
    let c2: { x: number; y: number };
    if (sa === sb) {
      // stay inside the gap between columns: a curve reaches 3/4 of its control points' offset
      const bulge = Math.min(COL_GAP * 0.6, 26 + Math.abs(end.y - start.y) * 0.03);
      c1 = { x: start.x - bulge, y: start.y };
      c2 = { x: end.x - bulge, y: end.y };
    } else {
      const dx = Math.max(40, Math.abs(end.x - start.x) / 2);
      c1 = { x: start.x + (sa === "right" ? dx : -dx), y: start.y };
      c2 = { x: end.x + (sb === "right" ? dx : -dx), y: end.y };
    }
    const mid = { x: (start.x + 3 * c1.x + 3 * c2.x + end.x) / 8, y: (start.y + 3 * c1.y + 3 * c2.y + end.y) / 8 };
    return {
      edge,
      path: `M${r(start.x)},${r(start.y)} C${r(c1.x)},${r(c1.y)} ${r(c2.x)},${r(c2.y)} ${r(end.x)},${r(end.y)}`,
      mid: { x: r(mid.x), y: r(mid.y) },
    };
  });
}

/** The points linked to a point, with how: for the detail panel and highlighting. */
export interface Neighbour {
  edge: GraphEdge;
  other: GraphNode;
  outgoing: boolean;
}

export function neighbours(graph: ArgumentGraph, id: string): Neighbour[] {
  return graph.edges.flatMap((edge): Neighbour[] => {
    if (edge.source === id && graph.nodes[edge.target]) return [{ edge, other: graph.nodes[edge.target], outgoing: true }];
    if (edge.target === id && graph.nodes[edge.source]) return [{ edge, other: graph.nodes[edge.source], outgoing: false }];
    return [];
  });
}
