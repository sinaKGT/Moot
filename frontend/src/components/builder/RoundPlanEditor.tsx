// The round plan: a template to start from, then each round's type, instruction and settings.

import { useState } from "react";
import { ROUND_TYPES, type DraftAction, type DraftRound } from "../../builder/draft";
import type { Library, RoundType, SpeakingOrder } from "../../types";

const ORDER_LABEL: Record<SpeakingOrder, string> = {
  fixed: "Fixed order",
  moderator: "Moderator picks",
  free: "Anyone, in any order",
};

const label = (type: RoundType) => ROUND_TYPES.find((t) => t.type === type)!;

export function roundSummary(round: DraftRound, decision: boolean): string {
  if (round.type === "vote") return decision ? "which option should we choose?" : "who made the strongest case?";
  const parts = [ORDER_LABEL[round.speaking_order].toLowerCase()];
  if (round.blind) parts.unshift("blind");
  if (round.turns_per_agent > 1) parts.push(`${round.turns_per_agent} turns each`);
  if (round.mentions_allowed) parts.push("@ allowed");
  if (round.max_words) parts.push(`≤${round.max_words} words`);
  return parts.join(" · ");
}

interface Props {
  library: Library;
  template: string;
  rounds: DraftRound[];
  decision: boolean;
  dispatch: (action: DraftAction) => void;
}

export function RoundPlanEditor({ library, template, rounds, decision, dispatch }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState<RoundType>("free");

  function chooseTemplate(key: string) {
    const plan = key === "example" ? library.example.round_plan : library.templates[key]?.plan;
    if (plan) dispatch({ type: "applyTemplate", template: key, rounds: plan.rounds });
    setOpen(null);
  }

  function changeType(round: DraftRound, type: RoundType) {
    // a round still using the old type's defaults takes the new type's defaults
    const before = label(round.type);
    const after = label(type);
    dispatch({
      type: "updateRound",
      key: round.key,
      patch: {
        type,
        name: round.name === before.label || !round.name.trim() ? after.label : round.name,
        instruction: round.instruction === before.instruction || !round.instruction.trim() ? after.instruction : round.instruction,
      },
    });
  }

  return (
    <section className="panel plan-editor" aria-labelledby="plan-title">
      <div className="plan-head">
        <h2 className="label" id="plan-title">
          Round plan
        </h2>
        <label className="inline-field">
          Format
          <select value={template} onChange={(e) => chooseTemplate(e.target.value)}>
            <option value="example">Example debate</option>
            {Object.entries(library.templates).map(([key, info]) => (
              <option key={key} value={key}>
                {info.name}
              </option>
            ))}
            {template === "custom" && <option value="custom">Custom</option>}
          </select>
        </label>
      </div>

      <ol className="plan-rounds">
        {rounds.map((round, index) => {
          const id = `round-${round.key}`;
          const editing = open === round.key;
          return (
            <li key={round.key} className={`plan-round${editing ? " open" : ""}`}>
              <div className="plan-round-head">
                <button
                  type="button"
                  className="plan-round-title"
                  aria-expanded={editing}
                  aria-controls={`${id}-edit`}
                  onClick={() => setOpen(editing ? null : round.key)}
                >
                  <b>
                    {index + 1} · {round.name || label(round.type).label}
                  </b>
                  <span>{roundSummary(round, decision)}</span>
                </button>
                <div className="plan-round-tools">
                  <button type="button" className="icon-button" aria-label={`Move ${round.name} earlier`} disabled={index === 0} onClick={() => dispatch({ type: "moveRound", key: round.key, by: -1 })}>
                    ↑
                  </button>
                  <button type="button" className="icon-button" aria-label={`Move ${round.name} later`} disabled={index === rounds.length - 1} onClick={() => dispatch({ type: "moveRound", key: round.key, by: 1 })}>
                    ↓
                  </button>
                  <button type="button" className="icon-button" aria-label={`Remove ${round.name}`} disabled={rounds.length === 1} onClick={() => dispatch({ type: "removeRound", key: round.key })}>
                    ×
                  </button>
                </div>
              </div>

              {editing && (
                <div className="plan-round-edit" id={`${id}-edit`}>
                  <div className="row">
                    <div className="field">
                      <label htmlFor={`${id}-type`}>Type</label>
                      <select id={`${id}-type`} value={round.type} onChange={(e) => changeType(round, e.target.value as RoundType)}>
                        {ROUND_TYPES.map((t) => (
                          <option key={t.type} value={t.type}>
                            {t.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor={`${id}-name`}>Name</label>
                      <input id={`${id}-name`} value={round.name} maxLength={60} onChange={(e) => dispatch({ type: "updateRound", key: round.key, patch: { name: e.target.value } })} />
                    </div>
                  </div>
                  {round.type !== "vote" ? (
                    <>
                      <div className="field">
                        <label htmlFor={`${id}-instruction`}>What agents should do</label>
                        <textarea id={`${id}-instruction`} rows={2} value={round.instruction} onChange={(e) => dispatch({ type: "updateRound", key: round.key, patch: { instruction: e.target.value } })} />
                      </div>
                      <div className="row">
                        <div className="field">
                          <label htmlFor={`${id}-order`}>Who speaks when</label>
                          <select id={`${id}-order`} value={round.speaking_order} onChange={(e) => dispatch({ type: "updateRound", key: round.key, patch: { speaking_order: e.target.value as SpeakingOrder } })}>
                            {(Object.keys(ORDER_LABEL) as SpeakingOrder[]).map((order) => (
                              <option key={order} value={order}>
                                {ORDER_LABEL[order]}
                              </option>
                            ))}
                          </select>
                        </div>
                        <div className="field">
                          <label htmlFor={`${id}-turns`}>Turns each</label>
                          <input id={`${id}-turns`} type="number" min={1} max={10} value={round.turns_per_agent} onChange={(e) => dispatch({ type: "updateRound", key: round.key, patch: { turns_per_agent: Math.min(10, Math.max(1, Number(e.target.value) || 1)) } })} />
                        </div>
                      </div>
                      <div className="row">
                        <label className="check">
                          <input type="checkbox" checked={round.blind} onChange={(e) => dispatch({ type: "updateRound", key: round.key, patch: { blind: e.target.checked } })} />
                          Blind: agents don't see each other's answers
                        </label>
                        <label className="check">
                          <input type="checkbox" checked={round.mentions_allowed} onChange={(e) => dispatch({ type: "updateRound", key: round.key, patch: { mentions_allowed: e.target.checked } })} />
                          Agents may ask each other with @
                        </label>
                      </div>
                      <div className="field narrow">
                        <label htmlFor={`${id}-words`}>Max words per turn (blank: the discussion's limit)</label>
                        <input
                          id={`${id}-words`}
                          type="number"
                          min={10}
                          max={2000}
                          value={round.max_words ?? ""}
                          onChange={(e) => dispatch({ type: "updateRound", key: round.key, patch: { max_words: e.target.value ? Number(e.target.value) : null } })}
                        />
                      </div>
                    </>
                  ) : (
                    <p className="hint">
                      Everyone votes at once.{" "}
                      {decision ? "The question is which option to choose." : "The question is who made the strongest case."} You can also start polls of
                      your own during the discussion.
                    </p>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>

      <div className="plan-add">
        <label className="sr-only" htmlFor="add-round-type">
          Type of round to add
        </label>
        <select id="add-round-type" value={adding} onChange={(e) => setAdding(e.target.value as RoundType)}>
          {ROUND_TYPES.map((t) => (
            <option key={t.type} value={t.type}>
              {t.label}
            </option>
          ))}
        </select>
        <button type="button" className="button small" onClick={() => dispatch({ type: "addRound", round: adding })}>
          + Add round
        </button>
      </div>
    </section>
  );
}
