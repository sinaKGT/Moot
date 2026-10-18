// A quick start until the team builder: a ready-made panel on any topic and model.

import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import type { ModelEntry, ProviderInfo, SessionSummary } from "../types";

const DEFAULT_TOPIC = "Should cities ban private cars from their centres?";
const PREFERRED_MODEL = "nvidia/nemotron-3-super-120b-a12b:free";

export function StartScreen({ onOpen }: { onOpen: (id: string) => void }) {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [provider, setProvider] = useState("");
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [model, setModel] = useState("");
  const [modelNote, setModelNote] = useState("");
  const [topic, setTopic] = useState("");
  const [moderateMyself, setModerateMyself] = useState(false);
  const [name, setName] = useState("");
  const [role, setRole] = useState("Moderator");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .providers()
      .then((found) => {
        setProviders(found);
        const usable = found.find((p) => p.key_set) ?? found.find((p) => p.local);
        if (usable) setProvider(usable.id);
      })
      .catch((e: Error) => setProblem(e.message));
    api.sessions().then(setSessions).catch(() => setSessions([]));
  }, []);

  useEffect(() => {
    if (!provider) return;
    let current = true;
    setModels([]);
    setModel("");
    setModelNote("Looking for models…");
    api
      .models(provider)
      .then((found) => {
        if (!current) return;
        const sorted = [...found].sort(
          (a, b) => Number(b.id.endsWith(":free")) - Number(a.id.endsWith(":free")) || a.id.localeCompare(b.id),
        );
        setModels(sorted);
        setModel(sorted.find((m) => m.id === PREFERRED_MODEL)?.id ?? sorted[0]?.id ?? "");
        setModelNote(
          sorted.length ? `${sorted.length} model${sorted.length === 1 ? "" : "s"}` : "No models found. Is a model loaded?",
        );
      })
      .catch((e: Error) => current && setModelNote(`Couldn't list models: ${e.message}`));
    return () => {
      current = false;
    };
  }, [provider]);

  async function start(event: FormEvent) {
    event.preventDefault();
    setProblem(null);
    setBusy(true);
    try {
      const session = await api.createDemo({
        topic: topic.trim() || undefined,
        provider,
        model,
        moderator: moderateMyself
          ? { kind: "human", display_name: name.trim(), role: role.trim() || "Moderator" }
          : undefined,
      });
      await api.control(session.id, "start");
      onOpen(session.id);
    } catch (e) {
      setProblem((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="screen">
      <header className="topbar">
        <div className="brand">
          <span className="wordmark">MOOT</span>
          <span className="divider" />
          <h1 className="topic">New discussion</h1>
        </div>
      </header>
      <div className="start">
        <form className="panel" onSubmit={start}>
          <h2 className="label">Quick start</h2>
          <p className="hint">A ready-made panel: Ava argues for, Ben against, and Cleo starts undecided.</p>
          <div className="field">
            <label htmlFor="topic">Topic</label>
            <input id="topic" value={topic} placeholder={DEFAULT_TOPIC} onChange={(e) => setTopic(e.target.value)} />
          </div>
          <div className="row">
            <div className="field">
              <label htmlFor="provider">Models from</label>
              <select id="provider" value={provider} onChange={(e) => setProvider(e.target.value)}>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                    {p.local ? " (this computer)" : ""}
                    {p.api_key_env && !p.key_set ? " (no key)" : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="model">Model</label>
              <select id="model" value={model} onChange={(e) => setModel(e.target.value)} disabled={!models.length}>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.id}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <p className="hint" aria-live="polite">{modelNote}</p>
          <fieldset className="field">
            <legend>Who moderates?</legend>
            <div className="choice">
              <label>
                <input type="radio" name="moderator" checked={!moderateMyself} onChange={() => setModerateMyself(false)} /> The AI moderator
              </label>
              <label>
                <input type="radio" name="moderator" checked={moderateMyself} onChange={() => setModerateMyself(true)} /> You
              </label>
            </div>
          </fieldset>
          {moderateMyself && (
            <div className="row">
              <div className="field">
                <label htmlFor="mod-name">Your name</label>
                <input id="mod-name" value={name} required maxLength={40} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="mod-role">Your role</label>
                <input id="mod-role" value={role} maxLength={60} placeholder="Judge, Journalist…" onChange={(e) => setRole(e.target.value)} />
              </div>
            </div>
          )}
          {problem && (
            <p className="problem" role="alert">
              {problem}
            </p>
          )}
          <button className="button primary" type="submit" disabled={busy || !model}>
            {busy ? "Starting…" : "Start discussion"}
          </button>
        </form>

        <section className="panel recent" aria-label="Discussions">
          <h2 className="label">Discussions</h2>
          {sessions.length === 0 ? (
            <p className="hint">None yet. Discussions last until the server stops.</p>
          ) : (
            sessions.map((s) => (
              <button key={s.id} type="button" className="session-link" onClick={() => onOpen(s.id)}>
                {s.topic}
                <small>
                  {s.status} · {s.agents.join(", ")}
                </small>
              </button>
            ))
          )}
        </section>
      </div>
    </div>
  );
}
