// Wording and geometry for the alliance monitor (pure, tested).

import type { Alliance, AllianceMonitor, AllianceStep } from "../types";

type Pair = AllianceMonitor["pairs"][number];

export const amount = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));

/** From -1 (only opposing each other) to 1 (only backing each other). */
export function pairScore(pair: Pair): number {
  const total = pair.support + pair.challenge;
  return total ? (pair.support - pair.challenge) / total : 0;
}

export type Stance = "backed" | "opposed" | "mixed" | "none";

export function pairStance(pair: Pair): Stance {
  if (pair.support + pair.challenge === 0) return "none";
  const score = pairScore(pair);
  return score >= 0.34 ? "backed" : score <= -0.34 ? "opposed" : "mixed";
}

export const STANCE_LABEL: Record<Stance, string> = {
  backed: "backed each other",
  opposed: "opposed",
  mixed: "mixed",
  none: "no exchanges yet",
};

export function pairCounts(pair: Pair): string {
  const parts = [];
  if (pair.support) parts.push(`support ${amount(pair.support)}`);
  if (pair.challenge) parts.push(`challenge ${amount(pair.challenge)}`);
  return parts.join(", ");
}

const listOf = (labels: string[]) =>
  labels.length > 1 ? `${labels.slice(0, -1).join(", ")} and ${labels.at(-1)}` : (labels[0] ?? "");

/** One line of an alliance's history, as the timeline shows it. */
export function stepLine(step: AllianceStep, first: boolean, alliance: Alliance, name: (id: string) => string): string {
  const who = step.agent_id ? name(step.agent_id) : "";
  const reason = step.reason ? `: “${step.reason}”` : "";
  switch (step.change) {
    case "proposed":
      return first
        ? `${who} offered ${listOf(step.targets.map(name))} an alliance${alliance.goal ? `: “${alliance.goal}”` : ""}`
        : `${who} invited ${listOf(step.targets.map(name))}`;
    case "accepted":
      return `${who} accepted`;
    case "refused":
      return `${who} refused${reason}`;
    case "left":
      return `${who} left${reason}`;
    case "dissolved":
      return "The alliance ended";
  }
}

/** The round an alliance became active, from its history. */
export function formedRound(alliance: Alliance): number | null {
  return alliance.history?.find((s) => s.change === "accepted")?.round_index ?? null;
}

/** Seats on an ellipse for the network drawing, the first at the top. */
export function ringPositions(ids: string[], width: number, height: number, margin = 48): Record<string, { x: number; y: number }> {
  const rx = width / 2 - margin;
  const ry = height / 2 - margin;
  return Object.fromEntries(
    ids.map((id, i) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, ids.length);
      return [id, { x: width / 2 + rx * Math.cos(angle), y: height / 2 + ry * Math.sin(angle) }];
    }),
  );
}
