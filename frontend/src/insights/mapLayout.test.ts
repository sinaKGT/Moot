import { describe, expect, it } from "vitest";
import type { ArgumentGraph, GraphEdge, GraphNode } from "../types";
import { CARD_W, COL_GAP, layoutMap, neighbours, type MapColumn } from "./mapLayout";

const node = (id: string, type: GraphNode["type"], speaker: string | null, round: number | null, extra: Partial<GraphNode> = {}): GraphNode => ({
  id,
  type,
  text: `${id} text`,
  speaker_id: speaker,
  round_index: round,
  message_id: `m-${id}`,
  addressed_to: [],
  sources: [],
  ...extra,
});
const edge = (id: string, type: GraphEdge["type"], source: string, target: string): GraphEdge => ({ id, type, source, target, speaker_id: null });

const columns: MapColumn[] = [
  { id: "ava", name: "Ava", color: "#60a5fa" },
  { id: "ben", name: "Ben", color: "#fb7185" },
  { id: "cleo", name: "Cleo", color: "#a3e635" },
  { id: "moderator", name: "Moderator", color: "#e2e8f0", optional: true },
  { id: "user", name: "You", color: "#e2e8f0", optional: true },
];

function graphOf(nodes: GraphNode[], edges: GraphEdge[] = []): ArgumentGraph {
  return { nodes: Object.fromEntries(nodes.map((n) => [n.id, n])), edges };
}

const debate = graphOf(
  [
    node("t", "topic", null, null),
    node("q1", "subquestion", "moderator", null),
    node("q2", "subquestion", "moderator", null),
    node("a1", "claim", "ava", 0),
    node("b1", "claim", "ben", 0),
    node("c1", "question", "cleo", 0, { addressed_to: ["ben"] }),
    node("b2", "claim", "ben", 1),
    node("b3", "evidence", "ben", 1),
    node("a2", "concession", "ava", 1),
  ],
  [
    edge("e1", "supports", "a1", "t"),
    edge("e2", "challenges", "b1", "t"),
    edge("e3", "challenges", "b1", "a1"),
    edge("e4", "answers", "a1", "q2"),
    edge("e5", "answers", "b2", "c1"),
    edge("e6", "supports", "b3", "b2"),
    edge("e7", "concedes_to", "a2", "b1"),
  ],
);

describe("the argument map layout", () => {
  it("gives every agent a column, and the moderator or you one only when they made points", () => {
    const layout = layoutMap(debate, columns);
    expect(layout.columns.map((c) => c.id)).toEqual(["ava", "ben", "cleo"]);
    const xs = layout.columns.map((c) => c.x);
    expect(xs).toEqual([...xs].sort((a, b) => a - b));
    const withYou = layoutMap(graphOf([...Object.values(debate.nodes), node("u1", "claim", "user", 1)]), columns);
    expect(withYou.columns.map((c) => c.id)).toEqual(["ava", "ben", "cleo", "user"]);
  });

  it("puts each round in its own row, and can show the map as it was after a round", () => {
    const layout = layoutMap(debate, columns);
    expect(layout.rows.map((r) => r.roundIndex)).toEqual([0, 1]);
    const ben = layout.nodes.filter((p) => p.column === "ben" && p.node.round_index === 1);
    expect(ben).toHaveLength(2); // stacked in the same cell
    expect(layout.rows[1].h).toBeGreaterThan(layout.rows[0].h);

    const early = layoutMap(debate, columns, 0);
    expect(early.rows.map((r) => r.roundIndex)).toEqual([0]);
    expect(early.nodes.map((p) => p.node.id)).toEqual(["a1", "b1", "c1"]);
    expect(early.edges.map((e) => e.edge.id)).toEqual(["e3"]);
  });

  it("never overlaps two cards", () => {
    const { nodes } = layoutMap(debate, columns);
    for (const a of nodes)
      for (const b of nodes) {
        if (a === b) continue;
        const apart = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
        expect(apart, `${a.node.id} overlaps ${b.node.id}`).toBe(true);
      }
  });

  it("shows links to the topic and sub-questions as badges, not lines", () => {
    const layout = layoutMap(debate, columns);
    const at = (id: string) => layout.nodes.find((p) => p.node.id === id)!;
    expect(at("a1").side).toBe("for");
    expect(at("b1").side).toBe("against");
    expect(at("a1").answers).toEqual([1]); // Q2
    expect(layout.edges.map((e) => e.edge.id)).toEqual(["e3", "e5", "e6", "e7"]);
    expect(layout.topic?.id).toBe("t");
    expect(layout.subquestions.map((n) => n.id)).toEqual(["q1", "q2"]);
  });

  it("marks a question open until someone answers it", () => {
    expect(layoutMap(debate, columns, 0).nodes.find((p) => p.node.id === "c1")!.open).toBe(true);
    expect(layoutMap(debate, columns).nodes.find((p) => p.node.id === "c1")!.open).toBe(false);
  });

  it("starts each line on the source card's edge and ends it on the target's", () => {
    const layout = layoutMap(debate, columns);
    const at = (id: string) => layout.nodes.find((p) => p.node.id === id)!;
    const ends = (id: string) => {
      const numbers = layout.edges.find((e) => e.edge.id === id)!.path.match(/-?\d+(\.\d+)?/g)!.map(Number);
      return { start: numbers.slice(0, 2), end: numbers.slice(-2) };
    };
    // Ben (middle column) challenges Ava (left column): leaves Ben's left side, enters Ava's right
    const challenge = ends("e3");
    expect(challenge.start[0]).toBe(at("b1").x);
    expect(challenge.end[0]).toBe(at("a1").x + CARD_W);
    // Ben's evidence supports Ben's claim in the same column: both on the left side
    const own = ends("e6");
    expect(own.start[0]).toBe(at("b3").x);
    expect(own.end[0]).toBe(at("b2").x);
    // a curve within one column stays in the gap before it
    const path = layout.edges.find((e) => e.edge.id === "e6")!.path.match(/-?\d+(\.\d+)?/g)!.map(Number);
    const controls = [path[2], path[4]];
    for (const x of controls) expect(at("b3").x - x).toBeLessThanOrEqual((COL_GAP / 2) * (4 / 3));
    for (const id of ["e3", "e6"]) {
      const { start } = ends(id);
      const source = at(layout.edges.find((e) => e.edge.id === id)!.edge.source);
      expect(start[1]).toBeGreaterThan(source.y);
      expect(start[1]).toBeLessThan(source.y + source.h);
    }
  });

  it("lists a point's links in both directions", () => {
    const found = neighbours(debate, "b1").map(({ edge, other, outgoing }) => [edge.type, other.id, outgoing]);
    expect(found).toEqual([
      ["challenges", "t", true],
      ["challenges", "a1", true],
      ["concedes_to", "a2", false],
    ]);
  });
});
