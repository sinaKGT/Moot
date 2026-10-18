// The conversation as glass cards, streaming as agents type.

import { useEffect, useRef, type ReactNode } from "react";
import {
  conclusionSections,
  pollCounts,
  splitConclusion,
  type DiscussionState,
  type MessageEntry,
  type Names,
} from "../session/reducer";
import type { Persona, Round } from "../types";

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The text with @mentions highlighted in the mentioned agent's colour. */
export function withMentions(text: string, agents: Persona[]): ReactNode[] {
  const labels = [...agents.map((a) => a.name), "all", "everyone"].sort((a, b) => b.length - a.length);
  const pattern = new RegExp(`(?<![\\p{L}\\p{N}@])@(${labels.map(escape).join("|")})(?![\\p{L}\\p{N}])`, "giu");
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    parts.push(text.slice(last, index));
    const agent = agents.find((a) => a.name.toLowerCase() === match[1].toLowerCase());
    parts.push(
      <span key={index} className="mention" style={{ color: agent?.color ?? "#5eead4" }}>
        {match[0]}
      </span>,
    );
    last = index + match[0].length;
  }
  parts.push(text.slice(last));
  return parts;
}

interface Props {
  state: DiscussionState;
  agents: Persona[];
  rounds: Round[];
  names: Names;
  human: boolean;
}

export function Transcript({ state, agents, rounds, names, human }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const colors = Object.fromEntries(agents.map((a) => [a.id, a.color]));
  const byId = Object.fromEntries(state.entries.filter((e): e is MessageEntry => e.kind === "message").map((e) => [e.id, e]));

  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });

  const card = (entry: MessageEntry) => {
    const color = colors[entry.speakerId] ?? "#e2e8f0";
    const asked = entry.replyTo ? byId[entry.replyTo] : undefined;
    const streaming = !entry.done;
    if (entry.purpose === "conclusion") {
      const { statement, question } = splitConclusion(entry.text);
      const sections = conclusionSections(statement);
      const decided = sections.some((s) => s.heading);
      return (
        <article key={entry.id} className="card conclusion">
          <div className="card-head">
            <span className="speaker">{names[entry.speakerId]}</span>
            <span className="tags">
              <span className="tag answer">conclusion</span>
            </span>
          </div>
          {decided ? (
            <div className="decision-report">
              {sections.map((section, index) => (
                <div key={index} className={section.heading === "Decision" ? "part main" : "part"}>
                  {section.heading && <div className="kicker">{section.heading.toUpperCase()}</div>}
                  <p>{section.text}</p>
                </div>
              ))}
              {streaming && !question && <span className="caret" />}
            </div>
          ) : (
            <p>
              {statement}
              {streaming && !question && <span className="caret" />}
            </p>
          )}
          {question && (
            <div className="audience">
              <div className="kicker">QUESTION FOR THE AUDIENCE</div>
              <p>{question}</p>
            </div>
          )}
        </article>
      );
    }
    return (
      <article key={entry.id} className={`card${streaming ? " live" : ""}`} aria-busy={streaming}>
        <div className="card-head">
          <span className="speaker" style={{ color }}>
            {names[entry.speakerId] ?? entry.speakerId}
          </span>
          {asked && <span className="to">→ {names[asked.speakerId] ?? ""}</span>}
          {entry.purpose === "summary" && <span className="to">· summary</span>}
          <span className="tags">
            {entry.tags.map((tag) => (
              <span key={tag} className={`tag ${tag}`}>
                {tag}
              </span>
            ))}
          </span>
        </div>
        <p>
          {withMentions(entry.text, agents)}
          {streaming && <span className="caret" style={{ color }} />}
        </p>
        {entry.tokens && (
          <div className="tokens">
            {entry.tokens.in} in · {entry.tokens.out} out tokens
          </div>
        )}
      </article>
    );
  };

  return (
    <div
      ref={box}
      className="transcript"
      aria-live="polite"
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}
    >
      {state.entries.length === 0 && <p className="notice">The discussion will appear here.</p>}
      {state.entries.map((entry) => {
        switch (entry.kind) {
          case "message":
            return card(entry);
          case "round":
            return (
              <div key={entry.id} className="round-mark">
                Round {entry.roundIndex + 1} · {rounds[entry.roundIndex]?.name}
              </div>
            );
          case "poll": {
            const view = state.polls[entry.pollId];
            if (!view) return null;
            const summary = view.results
              ? pollCounts(view)
                  .map((r) => `${r.label} ${r.votes}`)
                  .join(" · ")
              : "voting…";
            return (
              <div key={entry.id} className="notice alliance">
                ▤ Poll: {view.poll.question} — {summary}
              </div>
            );
          }
          case "notice":
            if (entry.tone === "rule" && !human) return null;
            return (
              <div key={entry.id} className={`notice ${entry.tone}`}>
                {entry.tone === "alliance" ? "◇ " : "· "}
                {entry.text}
              </div>
            );
        }
      })}
    </div>
  );
}
