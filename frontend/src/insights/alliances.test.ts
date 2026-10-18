import { describe, expect, it } from "vitest";
import type { Alliance } from "../types";
import { formedRound, pairCounts, pairStance, ringPositions, stepLine } from "./alliances";

const pair = (support: number, challenge: number) => ({ first: "a", second: "b", support, challenge, allied: false });

describe("the alliance monitor's wording", () => {
  it("says how two agents stand towards each other", () => {
    expect(pairStance(pair(0, 0))).toBe("none");
    expect(pairStance(pair(3, 1))).toBe("backed");
    expect(pairStance(pair(0, 4))).toBe("opposed");
    expect(pairStance(pair(1, 1))).toBe("mixed");
    expect(pairCounts(pair(2.5, 1))).toBe("support 2.5, challenge 1");
  });

  it("tells an alliance's story step by step", () => {
    const alliance: Alliance = {
      id: "x",
      goal: "Fair exemptions",
      proposed_by: "ava",
      members: ["ava", "cleo"],
      invited: [],
      status: "active",
      history: [
        { change: "proposed", agent_id: "ava", targets: ["cleo", "ben"], round_index: 1, reason: "" },
        { change: "accepted", agent_id: "cleo", targets: [], round_index: 1, reason: "" },
        { change: "refused", agent_id: "ben", targets: [], round_index: 2, reason: "I disagree" },
      ],
    };
    const name = (id: string) => ({ ava: "Ava", ben: "Ben", cleo: "Cleo" })[id] ?? id;
    const lines = alliance.history!.map((s, i) => stepLine(s, i === 0, alliance, name));
    expect(lines).toEqual(["Ava offered Cleo and Ben an alliance: “Fair exemptions”", "Cleo accepted", "Ben refused: “I disagree”"]);
    expect(formedRound(alliance)).toBe(1);
  });

  it("seats agents on a ring, the first at the top", () => {
    const seats = ringPositions(["a", "b", "c"], 300, 200);
    expect(seats.a.x).toBeCloseTo(150);
    expect(seats.a.y).toBeCloseTo(48);
    expect(seats.b.x).toBeGreaterThan(150);
    expect(seats.c.x).toBeLessThan(150);
  });
});
