// Setting up a discussion: the topic, who sits at the table, who moderates, the rules and the
// round plan. The draft is remembered in this browser, so the next discussion starts from it.

import { useEffect, useReducer, useState, type DragEvent, type ReactNode } from "react";
import { api } from "../../api";
import { Avatar, ModeratorMark } from "../../avatar";
import {
  applyProposal,
  draftFromConfig,
  draftReducer,
  initialDraft,
  loadDraft,
  MAX_AGENTS,
  MAX_OPTIONS,
  problems,
  saveDraft,
  toConfig,
  type Draft,
  type DraftAction,
} from "../../builder/draft";
import type { Library, Persona, ProviderInfo, SessionSummary, TeamProposal } from "../../types";
import { seatPositions } from "../Arena";
import { AgentCard } from "./AgentCard";
import { EmbeddingPicker, ModelPicker } from "./ModelPicker";
import { RoundPlanEditor } from "./RoundPlanEditor";
import { TeamDesigner } from "./TeamDesigner";

const PERSONA_TYPE = "application/x-moot-persona";
const TABLE = 520;
const pct = (n: number) => `${(n / TABLE) * 100}%`;

const PRESET_LABEL: Record<string, string> = {
  answer_first: "Answer first",
  one_point: "One point at a time",
  short_answers: "Short answers",
  evidence_required: "Evidence required",
  no_repetition: "No repetition",
  stay_on_topic: "Stay on topic",
  formal_tone: "Formal tone",
};

function orNothing(state: Draft | null, action: DraftAction): Draft | null {
  if (action.type === "replace") return action.draft;
  return state ? draftReducer(state, action) : state;
}

export function TeamBuilder({ onOpen }: { onOpen: (id: string) => void }) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [draft, dispatch] = useReducer(orNothing, null, loadDraft);
  const [tried, setTried] = useState(false);
  const [serverProblems, setServerProblems] = useState<string[]>([]);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState<string | null>(null); // the discussion whose setup was taken
  const [designing, setDesigning] = useState(false);
  // the designer's panel just taken in: what it said, and the draft before it (to undo)
  const [designed, setDesigned] = useState<{ proposal: TeamProposal; before: Draft } | null>(null);

  useEffect(() => {
    api
      .library()
      .then(setLibrary)
      .catch((e: Error) => setLoadProblem(e.message));
    api
      .providers()
      .then(setProviders)
      .catch((e: Error) => setLoadProblem(e.message));
    api.sessions().then(setSessions).catch(() => setSessions([]));
  }, []);

  useEffect(() => {
    if (library && !draft) dispatch({ type: "replace", draft: initialDraft(library) });
  }, [library, draft]);

  useEffect(() => {
    if (draft) saveDraft(draft);
    setServerProblems([]);
  }, [draft]);

  if (!draft || !library) {
    return (
      <div className="screen">
        <Topbar />
        <p className={loadProblem ? "problem center" : "hint center"} role={loadProblem ? "alert" : undefined}>
          {loadProblem ? `Couldn't reach the Moot server: ${loadProblem}` : "Loading…"}
        </p>
      </div>
    );
  }

  const found = tried ? [...problems(draft), ...serverProblems] : [];
  const selected = draft.agents.find((a) => a.key === draft.selected) ?? null;

  async function start() {
    if (!draft) return;
    setTried(true);
    if (problems(draft).length) return;
    setBusy(true);
    try {
      const session = await api.createSession(toConfig(draft));
      await api.control(session.id, "start");
      onOpen(session.id);
    } catch (e) {
      setServerProblems((e as Error).message.split("\n"));
      setBusy(false);
    }
  }

  function reset() {
    if (library) dispatch({ type: "replace", draft: { ...initialDraft(library), defaultModel: draft?.defaultModel ?? null } });
    setTried(false);
    setLoaded(null);
    setDesigned(null);
  }

  function takeProposal(proposal: TeamProposal) {
    if (!draft || !library) return;
    dispatch({ type: "replace", draft: applyProposal(draft, proposal, library) });
    setDesigned({ proposal, before: draft });
    setDesigning(false);
    setLoaded(null);
    setTried(false);
    window.scrollTo({ top: 0 });
  }

  async function reuse(id: string) {
    try {
      const view = await api.session(id);
      dispatch({ type: "replace", draft: draftFromConfig(view.state.config) });
      setLoaded(view.state.config.topic);
      setDesigned(null);
      setTried(false);
      window.scrollTo({ top: 0 });
    } catch (e) {
      setServerProblems([`Couldn't load that setup: ${(e as Error).message}`]);
      setTried(true);
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteSession(id);
      setSessions((current) => current.filter((s) => s.id !== id));
    } catch (e) {
      setServerProblems([`Couldn't delete it: ${(e as Error).message}`]);
      setTried(true);
    }
  }

  return (
    <div className="screen">
      <Topbar>
        <button type="button" className="button accent" onClick={() => setDesigning(true)}>
          Team Designer
        </button>
        <button type="button" className="button" onClick={reset}>
          Start over
        </button>
        <button type="button" className="button primary" onClick={start} disabled={busy}>
          {busy ? "Starting…" : "Start discussion"}
        </button>
      </Topbar>

      {loaded && (
        <p className="builder-loaded" role="status">
          This is the setup of “{loaded}”. Change anything, then start it again.
        </p>
      )}
      {designed && (
        <div className="builder-loaded designed" role="status">
          <p>
            <b>The designer's panel is in.</b> Review each card (press a seat to open it), change anything, then start.
            {designed.proposal.rationale && ` ${designed.proposal.rationale}`}
          </p>
          {designed.proposal.balance_notes.length > 0 && (
            <p className="balance-line">
              {designed.proposal.revised ? "Balance check (revised the panel): " : "Balance check: "}
              {designed.proposal.balance_notes.join(" ")}
            </p>
          )}
          <div className="designed-actions">
            <button
              type="button"
              className="link-button"
              onClick={() => {
                dispatch({ type: "replace", draft: designed.before });
                setDesigned(null);
              }}
            >
              Undo
            </button>
            <button type="button" className="link-button quiet" onClick={() => setDesigned(null)}>
              Dismiss
            </button>
          </div>
        </div>
      )}
      {designing && <TeamDesigner library={library} model={draft.defaultModel} onUse={takeProposal} onClose={() => setDesigning(false)} />}
      {found.length > 0 && (
        <div className="builder-problems" role="alert">
          <b>Before starting:</b>
          <ul>
            {found.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="builder">
        <LibraryPanel
          personas={library.personas}
          full={draft.agents.length >= MAX_AGENTS}
          onAdd={(persona) => dispatch({ type: "addAgent", persona })}
        >
          <History sessions={sessions} onOpen={onOpen} onReuse={reuse} onDelete={remove} />
        </LibraryPanel>

        <main className="builder-main">
          <TopicPanel draft={draft} dispatch={dispatch} library={library} />
          <div className="builder-middle">
            <TableView draft={draft} dispatch={dispatch} personas={library.personas} />
            <div className="builder-settings">
              <section className="panel" aria-labelledby="model-title">
                <h2 className="label" id="model-title">
                  Model
                </h2>
                <ModelPicker id="default-model" providers={providers} value={draft.defaultModel} onChange={(model) => dispatch({ type: "set", patch: { defaultModel: model } })} />
                <p className="hint tight">Used by every agent without its own model, the moderator and the argument map.</p>
                <h3 className="label sub">Meaning search in agents' files</h3>
                <EmbeddingPicker
                  providers={providers}
                  value={draft.embeddingModel}
                  auto={draft.embeddingAuto}
                  onChange={(model, byHand) =>
                    dispatch({ type: "set", patch: { embeddingModel: model, embeddingAuto: draft.embeddingAuto && !byHand } })
                  }
                />
              </section>
              <ModeratorPanel draft={draft} dispatch={dispatch} />
              <RulesPanel draft={draft} dispatch={dispatch} presets={library.rule_presets} />
            </div>
          </div>
          <RoundPlanEditor library={library} template={draft.template} rounds={draft.rounds} decision={draft.decision} dispatch={dispatch} />
        </main>

        {selected ? (
          <AgentCard
            key={selected.key}
            agent={selected}
            takenColors={draft.agents.filter((a) => a.key !== selected.key).map((a) => a.color)}
            providers={providers}
            defaultModel={draft.defaultModel}
            embeddingModel={draft.embeddingModel}
            canRemove={draft.agents.length > 1}
            onChange={(patch) => dispatch({ type: "updateAgent", key: selected.key, patch })}
            onRemove={() => dispatch({ type: "removeAgent", key: selected.key })}
          />
        ) : (
          <aside className="panel agent-card empty">
            <p className="hint">Choose an agent at the table to edit it, or add one from the library.</p>
          </aside>
        )}
      </div>
    </div>
  );
}

function Topbar({ children }: { children?: ReactNode }) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="wordmark">MOOT</span>
        <span className="divider" />
        <h1 className="topic">New discussion</h1>
      </div>
      {children && <div className="builder-actions">{children}</div>}
    </header>
  );
}

// ---------------------------------------------------------------- the library

interface LibraryProps {
  personas: Persona[];
  full: boolean;
  onAdd: (persona: Partial<Persona>) => void;
  children?: ReactNode;
}

function LibraryPanel({ personas, full, onAdd, children }: LibraryProps) {
  const drag = (event: DragEvent, index: number) => {
    event.dataTransfer.setData(PERSONA_TYPE, String(index));
    event.dataTransfer.effectAllowed = "copy";
  };
  return (
    <aside className="builder-side">
      <section className="panel" aria-labelledby="library-title">
        <h2 className="label" id="library-title">
          Agent library
        </h2>
        <p className="hint tight">Drag an agent to the table, or press it to add it.</p>
        <ul className="library">
          {personas.map((persona, index) => (
            <li key={persona.id}>
              <button
                type="button"
                className="library-item"
                draggable={!full}
                disabled={full}
                onDragStart={(e) => drag(e, index)}
                onClick={() => onAdd(persona)}
                aria-label={`Add ${persona.name}: ${persona.role}`}
              >
                <Avatar name={persona.name} color={persona.color} size={28} />
                <span>
                  <b>{persona.name}</b>
                  <small>{persona.role}</small>
                </span>
                <span className="add" aria-hidden="true">
                  +
                </span>
              </button>
            </li>
          ))}
        </ul>
        <button type="button" className="button small wide" disabled={full} onClick={() => onAdd({ name: "Agent" })}>
          + New agent
        </button>
        {full && <p className="hint tight">The table is full ({MAX_AGENTS} agents).</p>}
      </section>

      {children}
    </aside>
  );
}

// ---------------------------------------------------------------- earlier discussions

const SHOWN = 6;

export function statusLabel(s: SessionSummary): string {
  if (s.status === "finished") return s.end_reason === "stopped" ? "stopped" : s.end_reason === "error" ? "ended by an error" : "finished";
  return { setup: "not started", running: "live", paused: "paused" }[s.status];
}

interface HistoryProps {
  sessions: SessionSummary[];
  onOpen: (id: string) => void;
  onReuse: (id: string) => void;
  onDelete: (id: string) => void;
}

function History({ sessions, onOpen, onReuse, onDelete }: HistoryProps) {
  const [all, setAll] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  if (sessions.length === 0) return null;
  const shown = all ? sessions : sessions.slice(0, SHOWN);
  const when = (iso: string) =>
    new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  return (
    <section className="panel" aria-labelledby="sessions-title">
      <h2 className="label" id="sessions-title">
        Discussions
      </h2>
      <p className="hint tight">Saved on this computer.</p>
      <ul className="history">
        {shown.map((s) => (
          <li key={s.id}>
            <button type="button" className="session-link" onClick={() => onOpen(s.id)}>
              {s.topic}
              <small>
                <span className={`state ${s.status === "running" ? "live" : ""}`}>{statusLabel(s)}</span> · {when(s.created_at)} ·{" "}
                {s.agents.join(", ")}
              </small>
            </button>
            {confirming === s.id ? (
              <div className="history-actions confirm" role="group" aria-label="Delete for good?">
                <span>Delete for good?</span>
                <button type="button" className="link-button danger" onClick={() => onDelete(s.id)}>
                  Delete
                </button>
                <button type="button" className="link-button" onClick={() => setConfirming(null)}>
                  Keep
                </button>
              </div>
            ) : (
              <div className="history-actions">
                <button type="button" className="link-button" onClick={() => onReuse(s.id)}>
                  Use this setup
                </button>
                <button type="button" className="link-button quiet" onClick={() => setConfirming(s.id)}>
                  Delete
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {sessions.length > SHOWN && (
        <button type="button" className="button small wide" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${sessions.length}`}
        </button>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- topic and decision

function TopicPanel({ draft, dispatch, library }: { draft: Draft; dispatch: (a: DraftAction) => void; library: Library }) {
  const [option, setOption] = useState("");
  const [showBackground] = useState(() => draft.background.trim() !== ""); // open it once, if filled
  const add = () => {
    dispatch({ type: "addOption", option });
    setOption("");
  };
  const decisionPlan = library.templates.decision;
  return (
    <section className="panel topic-panel" aria-label="Topic">
      <div className="field">
        <label htmlFor="topic">{draft.decision ? "What should be decided?" : "Topic"}</label>
        <input
          id="topic"
          className="topic-input"
          value={draft.topic}
          maxLength={2000}
          placeholder={draft.decision ? "Which city should we open our next office in?" : library.example.topic}
          onChange={(e) => dispatch({ type: "set", patch: { topic: e.target.value } })}
        />
      </div>
      <details className="advanced" open={showBackground}>
        <summary>Background: facts everyone should know</summary>
        <textarea
          rows={4}
          aria-label="Background"
          value={draft.background}
          placeholder="Budget, constraints, data, what has been tried…"
          onChange={(e) => dispatch({ type: "set", patch: { background: e.target.value } })}
        />
      </details>

      <label className="check">
        <input type="checkbox" checked={draft.decision} onChange={(e) => dispatch({ type: "set", patch: { decision: e.target.checked } })} />
        This is a decision between options
      </label>
      {draft.decision && (
        <div className="decision">
          <p className="hint tight">
            Agents weigh the options, the vote asks which one to choose, and the conclusion gives the decision, its risks and next steps.
          </p>
          <ul className="options" aria-label="Options">
            {draft.options.map((o, index) => (
              <li key={o} className="option-chip">
                <span className="letter">{String.fromCharCode(65 + index)}</span>
                {o}
                <button type="button" aria-label={`Remove ${o}`} onClick={() => dispatch({ type: "removeOption", index })}>
                  ×
                </button>
              </li>
            ))}
          </ul>
          {draft.options.length < MAX_OPTIONS && (
            <div className="option-add">
              <label className="sr-only" htmlFor="option">
                New option
              </label>
              <input
                id="option"
                value={option}
                maxLength={120}
                placeholder={draft.options.length ? "Another option" : "First option, e.g. Lisbon"}
                onChange={(e) => setOption(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    add();
                  }
                }}
              />
              <button type="button" className="button small" onClick={add} disabled={!option.trim()}>
                Add option
              </button>
            </div>
          )}
          {decisionPlan && draft.template !== "decision" && (
            <p className="hint tight">
              The Decision format suits this: options, criteria, weighing, a pre-mortem, then a vote.{" "}
              <button type="button" className="link-button" onClick={() => dispatch({ type: "applyTemplate", template: "decision", rounds: decisionPlan.plan.rounds })}>
                Use it
              </button>
            </p>
          )}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- the table

function TableView({ draft, dispatch, personas }: { draft: Draft; dispatch: (a: DraftAction) => void; personas: Persona[] }) {
  const [over, setOver] = useState(false);
  const seats = seatPositions(draft.agents.map((a) => a.key));
  const full = draft.agents.length >= MAX_AGENTS;
  const moderatorName = draft.moderator.kind === "human" ? draft.moderator.display_name.trim() || "You" : "Moderator";

  const accepts = (event: DragEvent) => !full && event.dataTransfer.types.includes(PERSONA_TYPE);
  const drop = (event: DragEvent) => {
    setOver(false);
    if (!accepts(event)) return;
    event.preventDefault();
    const persona = personas[Number(event.dataTransfer.getData(PERSONA_TYPE))];
    if (persona) dispatch({ type: "addAgent", persona });
  };

  return (
    <section
      className={`panel table-panel${over ? " over" : ""}`}
      aria-label="The table"
      onDragOver={(e) => {
        if (accepts(e)) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
          setOver(true);
        }
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <div className="builder-table">
        <div className="table-disc" aria-hidden="true" />
        <div className="builder-centre">
          <span>
            {draft.agents.length} agent{draft.agents.length === 1 ? "" : "s"} · {draft.rounds.length} round{draft.rounds.length === 1 ? "" : "s"}
          </span>
          {!full && (
            <button type="button" className="button small accent" onClick={() => dispatch({ type: "addAgent", persona: { name: "Agent" } })}>
              + Seat
            </button>
          )}
        </div>
        <div className="builder-seat moderator" style={{ left: pct(seats.moderator.x), top: pct(seats.moderator.y) }}>
          <span className="seat-disc">
            <ModeratorMark size={30} color={draft.moderator.kind === "human" ? "#fbbf24" : "#e2e8f0"} />
          </span>
          <span className="seat-name">{moderatorName}</span>
        </div>
        {draft.agents.map((agent) => (
          <button
            key={agent.key}
            type="button"
            className={`builder-seat${draft.selected === agent.key ? " selected" : ""}`}
            style={{ left: pct(seats[agent.key].x), top: pct(seats[agent.key].y), ["--seat" as string]: agent.color }}
            aria-pressed={draft.selected === agent.key}
            aria-label={`Edit ${agent.name || "unnamed agent"}`}
            onClick={() => dispatch({ type: "selectAgent", key: agent.key })}
          >
            <span className="seat-disc">
              <Avatar name={agent.name || "?"} color={agent.color} size={30} />
            </span>
            <span className="seat-name">{agent.name || "Unnamed"}</span>
            {agent.role && <span className="seat-role">{agent.role}</span>}
          </button>
        ))}
      </div>
      {over && <p className="drop-hint">Drop to seat this agent</p>}
    </section>
  );
}

// ---------------------------------------------------------------- moderator and rules

function ModeratorPanel({ draft, dispatch }: { draft: Draft; dispatch: (a: DraftAction) => void }) {
  const human = draft.moderator.kind === "human" ? draft.moderator : null;
  return (
    <fieldset className="panel">
      <legend className="label">Moderator</legend>
      <div className="choice">
        <label>
          <input type="radio" name="moderator" checked={!human} onChange={() => dispatch({ type: "set", patch: { moderator: { kind: "ai" } } })} /> AI moderator
        </label>
        <label>
          <input
            type="radio"
            name="moderator"
            checked={!!human}
            onChange={() => dispatch({ type: "set", patch: { moderator: { kind: "human", display_name: "", role: "Moderator" } } })}
          />{" "}
          You
        </label>
      </div>
      {human ? (
        <div className="row spaced">
          <div className="field">
            <label htmlFor="mod-name">Your name</label>
            <input id="mod-name" value={human.display_name} maxLength={40} onChange={(e) => dispatch({ type: "set", patch: { moderator: { ...human, display_name: e.target.value } } })} />
          </div>
          <div className="field">
            <label htmlFor="mod-role">Your role</label>
            <input id="mod-role" value={human.role} maxLength={60} placeholder="Judge, Journalist…" onChange={(e) => dispatch({ type: "set", patch: { moderator: { ...human, role: e.target.value } } })} />
          </div>
        </div>
      ) : (
        <p className="hint tight spaced">It opens, picks speakers, sums up each round and writes the conclusion.</p>
      )}
    </fieldset>
  );
}

function RulesPanel({ draft, dispatch, presets }: { draft: Draft; dispatch: (a: DraftAction) => void; presets: Record<string, string> }) {
  const [rule, setRule] = useState("");
  const add = () => {
    dispatch({ type: "addRule", rule });
    setRule("");
  };
  return (
    <fieldset className="panel">
      <legend className="label">Rules</legend>
      <div className="rule-presets">
        {Object.entries(presets).map(([key, text]) => (
          <label key={key} className="check" title={text}>
            <input type="checkbox" checked={draft.rules.presets.includes(key)} onChange={() => dispatch({ type: "togglePreset", preset: key })} />
            {PRESET_LABEL[key] ?? key}
          </label>
        ))}
      </div>
      {draft.rules.custom.length > 0 && (
        <ul className="custom-rules">
          {draft.rules.custom.map((r, index) => (
            <li key={`${index}-${r}`}>
              <span>{r}</span>
              <button type="button" className="icon-button" aria-label={`Remove rule: ${r}`} onClick={() => dispatch({ type: "removeRule", index })}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="option-add">
        <label className="sr-only" htmlFor="custom-rule">
          Your own rule
        </label>
        <input
          id="custom-rule"
          value={rule}
          placeholder="Your own rule, e.g. Cite a number"
          onChange={(e) => setRule(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <button type="button" className="button small" onClick={add} disabled={!rule.trim()}>
          Add
        </button>
      </div>
      <div className="row spaced">
        <div className="field">
          <label htmlFor="max-words">Max words per turn</label>
          <input
            id="max-words"
            type="number"
            min={10}
            max={2000}
            placeholder="No limit"
            value={draft.rules.max_words_per_turn ?? ""}
            onChange={(e) => dispatch({ type: "setMaxWords", words: e.target.value ? Number(e.target.value) : null })}
          />
        </div>
        <label className="check self-end">
          <input type="checkbox" checked={draft.allowAlliances} onChange={(e) => dispatch({ type: "set", patch: { allowAlliances: e.target.checked } })} />
          Agents may form alliances
        </label>
      </div>
    </fieldset>
  );
}
