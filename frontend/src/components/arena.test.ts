import { describe, expect, it } from "vitest";
import { beamPath, seatPositions } from "./Arena";

const CENTRE = 260;

/** Points along a quadratic path "Mx,y Qcx,cy ex,ey". */
function points(path: string): { x: number; y: number }[] {
  const [sx, sy, cx, cy, ex, ey] = path.match(/-?\d+(\.\d+)?/g)!.map(Number);
  return Array.from({ length: 21 }, (_, i) => {
    const t = i / 20;
    return {
      x: (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * ex,
      y: (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * ey,
    };
  });
}

describe("the round table", () => {
  it("puts the moderator at the head and spreads the agents around", () => {
    const seats = seatPositions(["a", "b", "c"]);
    expect(seats.moderator.y).toBeLessThan(seats.b.y);
    expect(Math.round(seats.a.y)).toBe(CENTRE); // right
    expect(Math.round(seats.c.y)).toBe(CENTRE); // left
  });

  it("never draws a beam across the table, whichever seats it joins", () => {
    for (const count of [2, 3, 5, 8]) {
      const ids = Array.from({ length: count }, (_, i) => `agent${i}`);
      const seats = seatPositions(ids);
      const all = ["moderator", ...ids];
      for (const from of all) {
        for (const to of all) {
          if (from === to) continue;
          for (const lane of [-24, -14, 0, 14]) {
            const closest = Math.min(...points(beamPath(seats[from], seats[to], lane)).map((p) => Math.hypot(p.x - CENTRE, p.y - CENTRE)));
            expect(closest).toBeGreaterThan(118); // the table's radius
          }
        }
      }
    }
  });
});
