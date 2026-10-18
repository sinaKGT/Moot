// The Team Designer: describe the goal, get a proposed panel, format and options, then take
// it into the team builder to review and edit each card before starting.

import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { Avatar } from "../../avatar";
import { AGENT_COLORS } from "../../builder/draft";
import type { Library, ModelRef, TeamProposal } from "../../types";

const EXAMPLES = [
  "A political discussion: four different views on immigration policy",
  "Should our research lab move to a four-day week?",
  "Choose where to open our next office: Lisbon, Berlin or staying fully remote",
];

interface Props {
  library: Library;
  model: ModelRef | null;
  onUse: (proposal: TeamProposal) => void;
  onClose: () => void;
}

export function TeamDesigner({ library, model, onUse, onClose }: Props) {
  const [goal, setGoal] = useState("");
  const [count, setCount] = useState<number | null>(null);
  const [balance, setBalance] = useState(true);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [proposal, setProposal] = useState<TeamProposal | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const run = useRef(0); // ignore an answer that arrives after closing or a new try
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    box.current?.focus();
    const escape = (event: KeyboardEvent) => event.key === "Escape" && close.current();
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("keydown", escape);
      run.current += 1;
    };
  }, []);

  async function design() {
    if (!model || !goal.trim()) return;
    const mine = ++run.current;
    setBusy(true);
    setProblem(null);
    setProposal(null);
    try {
      const found = await api.design({ goal: goal.trim(), provider: model.provider, model: model.model, agents: count, balance_check: balance });
      if (mine === run.current) setProposal(found);
    } catch (e) {
      if (mine === run.current) setProblem((e as Error).message);
    } finally {
      if (mine === run.current) setBusy(false);
    }
  }

  const formatName = (key: string) => library.templates[key]?.name ?? key;

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <section className="dialog panel" role="dialog" aria-modal="true" aria-labelledby="designer-title">
        <div className="dialog-head">
          <h2 id="designer-title">Team Designer</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>

        {!proposal ? (
          <>
            <p className="hint tight">
              Describe what the discussion is for. The designer proposes a panel with clearly different views, the format and, for a decision, the
              options. You can change everything before starting.
            </p>
            <div className="field">
              <label htmlFor="goal">Your goal</label>
              <textarea
                id="goal"
                ref={box}
                rows={4}
                maxLength={4000}
                value={goal}
                placeholder="What should be discussed or decided, and who should be at the table?"
                onChange={(e) => setGoal(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) design();
                }}
              />
            </div>
            <div className="examples" aria-label="Examples">
              {EXAMPLES.map((example) => (
                <button key={example} type="button" className="example" onClick={() => setGoal(example)}>
                  {example}
                </button>
              ))}
            </div>
            <div className="row spaced">
              <div className="field">
                <label htmlFor="designer-count">Agents</label>
                <select id="designer-count" value={count ?? ""} onChange={(e) => setCount(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">As the goal needs</option>
                  {[2, 3, 4, 5, 6, 7, 8].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </div>
              <label className="check self-end">
                <input type="checkbox" checked={balance} onChange={(e) => setBalance(e.target.checked)} />
                Balance check: a second pass that makes sure the views are distinct and fair
              </label>
            </div>
            <p className="hint tight">{model ? `Uses the discussion's model: ${model.model}.` : "Choose the discussion's model first, in the Model panel."}</p>
            {problem && (
              <p className="problem" role="alert">
                {problem}
              </p>
            )}
            <div className="dialog-actions">
              <button type="button" className="button" onClick={onClose}>
                Cancel
              </button>
              <button type="button" className="button primary" disabled={!model || !goal.trim() || busy} onClick={design}>
                {busy ? (balance ? "Designing and checking…" : "Designing…") : "Design the panel"}
              </button>
            </div>
            {busy && <p className="hint tight center-text">This can take a minute with some models.</p>}
          </>
        ) : (
          <div className="proposal" aria-live="polite">
            <div className="proposal-topic">
              <span className="kicker">{formatName(proposal.format)}</span>
              <p>{proposal.topic}</p>
            </div>
            {proposal.options.length > 0 && (
              <ul className="options" aria-label="Options">
                {proposal.options.map((option, i) => (
                  <li key={option} className="option-chip">
                    <span className="letter">{String.fromCharCode(65 + i)}</span>
                    {option}
                  </li>
                ))}
              </ul>
            )}
            <ul className="proposal-agents">
              {proposal.agents.map((agent, i) => (
                <li key={agent.id}>
                  <Avatar name={agent.name} color={AGENT_COLORS[i % AGENT_COLORS.length]} size={26} />
                  <div>
                    <b>{agent.name}</b>
                    {agent.role && <span className="role"> · {agent.role}</span>}
                    <p>{agent.viewpoint}</p>
                  </div>
                </li>
              ))}
            </ul>
            {proposal.rationale && <p className="hint tight">{proposal.rationale}</p>}
            {proposal.balance_notes.length > 0 && (
              <div className={`balance${proposal.revised ? " revised" : ""}`}>
                <span className="kicker">{proposal.revised ? "Balance check · revised the panel" : "Balance check"}</span>
                <ul>
                  {proposal.balance_notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="dialog-actions">
              <button type="button" className="button" onClick={() => setProposal(null)}>
                Change the goal
              </button>
              <button type="button" className="button" onClick={design} disabled={busy}>
                {busy ? "Designing…" : "Try again"}
              </button>
              <button type="button" className="button primary" onClick={() => onUse(proposal)} disabled={busy}>
                Use this panel
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
