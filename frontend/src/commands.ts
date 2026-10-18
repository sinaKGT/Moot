// What the user types: a message, or a /command. Also the autocomplete for @names and /commands.

export type Action =
  | { kind: "say"; text: string }
  | { kind: "poll"; question?: string; options?: string[] }
  | { kind: "control"; control: "pause" | "resume" | "stop" | "finish" | "next-round" | "end-round" }
  | { kind: "floor"; agent: string | null }
  | { kind: "remind"; agent: string; text: string }
  | { kind: "invalid"; message: string };

export interface CommandInfo {
  name: string;
  hint: string;
  moderatorOnly?: boolean;
}

export const COMMANDS: CommandInfo[] = [
  { name: "/poll", hint: "who made the strongest case? or /poll Question | option | option" },
  { name: "/pause", hint: "pause after the current turn" },
  { name: "/resume", hint: "carry on" },
  { name: "/next", hint: "start the next round, or move on" },
  { name: "/end", hint: "end this round" },
  { name: "/finish", hint: "end the discussion (with a conclusion)" },
  { name: "/stop", hint: "stop now (no conclusion)" },
  { name: "/floor", hint: "give someone the floor: /floor Name", moderatorOnly: true },
  { name: "/remind", hint: "remind an agent: /remind Name text", moderatorOnly: true },
];

export const DEFAULT_POLL_OPTIONS = ["Yes", "No", "Not sure"];

/** The agent named at the start of `text`, longest name first: "Domain Expert keep it short". */
export function splitName(text: string, names: string[]): { name: string; rest: string } | null {
  const cleaned = text.trim().replace(/^@/, "");
  const sorted = [...names].sort((a, b) => b.length - a.length);
  for (const name of sorted) {
    const head = cleaned.slice(0, name.length);
    const after = cleaned.charAt(name.length);
    if (head.toLowerCase() === name.toLowerCase() && (after === "" || !/[\p{L}\p{N}]/u.test(after))) {
      return { name, rest: cleaned.slice(name.length).replace(/^[\s,:-]+/, "") };
    }
  }
  return null;
}

export function parseInput(input: string, names: string[]): Action {
  const text = input.trim();
  if (!text.startsWith("/")) return { kind: "say", text };
  const [command, ...words] = text.split(/\s+/);
  const rest = text.slice(command.length).trim();
  switch (command.toLowerCase()) {
    case "/poll": {
      if (!rest) return { kind: "poll" };
      const [question, ...options] = rest.split("|").map((part) => part.trim());
      const chosen = options.filter(Boolean);
      if (chosen.length === 1) return { kind: "invalid", message: "A poll needs at least two options." };
      return { kind: "poll", question, options: chosen.length ? chosen : DEFAULT_POLL_OPTIONS };
    }
    case "/pause":
      return { kind: "control", control: "pause" };
    case "/resume":
      return { kind: "control", control: "resume" };
    case "/next":
      return { kind: "control", control: "next-round" };
    case "/end":
      return { kind: "control", control: "end-round" };
    case "/finish":
      return { kind: "control", control: "finish" };
    case "/stop":
      return { kind: "control", control: "stop" };
    case "/floor": {
      if (!words.length) return { kind: "floor", agent: null };
      const found = splitName(rest, names);
      return found ? { kind: "floor", agent: found.name } : { kind: "invalid", message: `Nobody called ${rest}.` };
    }
    case "/remind": {
      const found = splitName(rest, names);
      if (!found || !found.rest) return { kind: "invalid", message: "Write /remind Name what to remember." };
      return { kind: "remind", agent: found.name, text: found.rest };
    }
    default:
      return { kind: "invalid", message: `Unknown command ${command}. Try /poll, /pause or /next.` };
  }
}

export interface Suggestion {
  label: string;
  detail?: string;
  /** Text that replaces input[from, to). */
  insert: string;
  from: number;
  to: number;
}

const EVERYONE = { name: "all", detail: "ask everyone" };

/** Autocomplete for the text before the caret: @names anywhere, /commands at the start, and
 * agent names after /floor and /remind. */
export function suggestions(input: string, caret: number, names: string[], human: boolean): Suggestion[] {
  const before = input.slice(0, caret);
  const starts = (option: string, typed: string) => option.toLowerCase().startsWith(typed.toLowerCase());

  const command = /^\/\S*$/.exec(before);
  if (command) {
    return COMMANDS.filter((c) => (human || !c.moderatorOnly) && starts(c.name, before)).map((c) => ({
      label: c.name,
      detail: c.hint,
      insert: `${c.name} `,
      from: 0,
      to: caret,
    }));
  }

  const argument = /^(\/(?:floor|remind)\s+)(@?)(.*)$/i.exec(before);
  if (argument) {
    const [, prefix, , typed] = argument;
    return names
      .filter((name) => starts(name, typed))
      .map((name) => ({ label: name, insert: `${name} `, from: prefix.length, to: caret }));
  }

  const at = before.lastIndexOf("@");
  if (at >= 0 && (at === 0 || /\s/.test(before[at - 1]))) {
    const typed = before.slice(at + 1);
    const options = [...names.map((name) => ({ name, detail: undefined as string | undefined })), EVERYONE];
    return options
      .filter((option) => starts(option.name, typed))
      .map((option) => ({
        label: `@${option.name}`,
        detail: option.detail,
        insert: `@${option.name} `,
        from: at,
        to: caret,
      }));
  }
  return [];
}
