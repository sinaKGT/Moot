import { describe, expect, it } from "vitest";
import { parseInput, splitName, suggestions } from "./commands";

const names = ["Ava", "Ben", "Domain Expert"];

describe("parseInput", () => {
  it("sends plain text as a message", () => {
    expect(parseInput("  @Ben why?  ", names)).toEqual({ kind: "say", text: "@Ben why?" });
  });

  it("reads polls", () => {
    expect(parseInput("/poll", names)).toEqual({ kind: "poll" });
    expect(parseInput("/poll Ban cars? | Now | Later", names)).toEqual({ kind: "poll", question: "Ban cars?", options: ["Now", "Later"] });
    expect(parseInput("/poll Is it fair?", names)).toEqual({ kind: "poll", question: "Is it fair?", options: ["Yes", "No", "Not sure"] });
    expect(parseInput("/poll Q | only", names).kind).toBe("invalid");
  });

  it("reads controls, the floor and reminders", () => {
    expect(parseInput("/next", names)).toEqual({ kind: "control", control: "next-round" });
    expect(parseInput("/END", names)).toEqual({ kind: "control", control: "end-round" });
    expect(parseInput("/floor", names)).toEqual({ kind: "floor", agent: null });
    expect(parseInput("/floor @domain expert", names)).toEqual({ kind: "floor", agent: "Domain Expert" });
    expect(parseInput("/floor Zed", names)).toEqual({ kind: "invalid", message: "Nobody called Zed." });
    expect(parseInput("/remind Ben keep it short", names)).toEqual({ kind: "remind", agent: "Ben", text: "keep it short" });
    expect(parseInput("/remind Ben", names).kind).toBe("invalid");
    expect(parseInput("/dance", names).kind).toBe("invalid");
  });
});

describe("splitName", () => {
  it("prefers the longest name and needs a word break", () => {
    expect(splitName("Domain Expert, be brief", ["Domain", "Domain Expert"])).toEqual({ name: "Domain Expert", rest: "be brief" });
    expect(splitName("Avalon", names)).toBeNull();
  });
});

describe("suggestions", () => {
  const labels = (text: string, human = false, caret = text.length) => suggestions(text, caret, names, human).map((s) => s.label);

  it("suggests agents after @, including everyone", () => {
    expect(labels("Hi @")).toEqual(["@Ava", "@Ben", "@Domain Expert", "@all"]);
    expect(labels("Hi @d")).toEqual(["@Domain Expert"]);
    expect(labels("Hi @Domain E")).toEqual(["@Domain Expert"]);
    expect(labels("Hi @Ben ")).toEqual([]);
    expect(labels("mail@ben")).toEqual([]);
  });

  it("replaces just the typed name", () => {
    const [first] = suggestions("Hi @be", 6, names, false);
    expect(first).toMatchObject({ insert: "@Ben ", from: 3, to: 6 });
  });

  it("suggests commands at the start, moderator ones only when you moderate", () => {
    expect(labels("/p")).toEqual(["/poll", "/pause"]);
    expect(labels("/f")).toEqual(["/finish"]);
    expect(labels("/f", true)).toEqual(["/finish", "/floor"]);
    expect(labels("say /p")).toEqual([]);
  });

  it("suggests agents after /floor and /remind", () => {
    expect(labels("/floor ", true)).toEqual(["Ava", "Ben", "Domain Expert"]);
    expect(labels("/remind b", true)).toEqual(["Ben"]);
    expect(labels("/remind Ben keep", true)).toEqual([]);
    const [first] = suggestions("/floor a", 8, names, true);
    expect(first).toMatchObject({ insert: "Ava ", from: 7, to: 8 });
  });
});
