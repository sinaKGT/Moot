// An agent's own files: drop them in, watch them become a knowledge graph, and try what the
// agent would be told. Only this agent is briefed from them, and it cites them by name.

import { useEffect, useRef, useState, type DragEvent } from "react";
import { api } from "../../api";
import type { KnowledgeBase, KnowledgeDocument, KnowledgeSearch, ModelRef } from "../../types";

const ACCEPT = ".pdf,.docx,.txt,.md,.markdown";
const READABLE = /\.(pdf|docx|txt|md|markdown)$/i;
const MAX_GRAPH_PASSAGES = 400;

export function documentStatus(d: KnowledgeDocument): string {
  if (d.status === "failed") return d.error ?? "Failed";
  if (d.status === "queued") return "Waiting…";
  if (d.status === "ready") return `Ready · ${d.chunks_total} passage${d.chunks_total === 1 ? "" : "s"}`;
  switch (d.stage) {
    case "reading":
      return "Reading…";
    case "embedding":
      return "Embedding passages…";
    case "mapping": {
      const total = Math.min(d.chunks_total, MAX_GRAPH_PASSAGES);
      return `Mapping into the graph: ${d.chunks_mapped + d.chunks_failed} of ${total}`;
    }
    case "linking":
      return "Linking entities and facts…";
    default:
      return "Indexing…";
  }
}

const busy = (base: KnowledgeBase | null) => !!base?.documents.some((d) => d.status === "queued" || d.status === "indexing");

interface Props {
  agentName: string;
  knowledge: string | null;
  embedding: ModelRef | null;
  model: ModelRef | null;
  onAttach: (id: string | null) => void;
}

export function KnowledgePanel({ agentName, knowledge, embedding, model, onAttach }: Props) {
  const [base, setBase] = useState<KnowledgeBase | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [over, setOver] = useState(false);
  const [question, setQuestion] = useState("");
  const [found, setFound] = useState<KnowledgeSearch | null>(null);
  const [searching, setSearching] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  // load the knowledge base, and keep refreshing while files are being indexed
  useEffect(() => {
    if (!knowledge) {
      setBase(null);
      return;
    }
    let current = true;
    let timer = 0;
    const load = () =>
      api.knowledge
        .get(knowledge)
        .then((loaded) => {
          if (!current) return;
          setBase(loaded);
          if (busy(loaded)) timer = window.setTimeout(load, 1500);
        })
        .catch((e: Error) => {
          if (!current) return;
          setProblem(e.message.startsWith("No knowledge base") ? "These files no longer exist." : e.message);
          setBase(null);
        });
    load();
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [knowledge, uploading]);

  async function add(files: File[]) {
    const readable = files.filter((f) => READABLE.test(f.name));
    const skipped = files.length - readable.length;
    setProblem(skipped ? `Moot reads PDF, DOCX, TXT and MD files; ${skipped} other file${skipped === 1 ? " was" : "s were"} skipped.` : null);
    if (!readable.length) return;
    setUploading(true);
    try {
      let id = knowledge;
      if (!id || !base) {
        const created = await api.knowledge.create(`${agentName || "Agent"}'s files`, embedding);
        id = created.id;
        onAttach(id);
      }
      for (const file of readable) await api.knowledge.upload(id, file, model);
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setUploading(false);
    }
  }

  async function removeFile(documentId: string) {
    if (!knowledge) return;
    try {
      await api.knowledge.removeFile(knowledge, documentId);
      setBase(await api.knowledge.get(knowledge));
      setFound(null);
    } catch (e) {
      setProblem((e as Error).message);
    }
  }

  async function deleteAll() {
    if (knowledge) await api.knowledge.remove(knowledge).catch(() => undefined);
    setConfirming(false);
    setFound(null);
    onAttach(null);
  }

  async function ask() {
    if (!knowledge || !question.trim()) return;
    setSearching(true);
    try {
      setFound(await api.knowledge.search(knowledge, question.trim()));
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setSearching(false);
    }
  }

  const drop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    void add([...event.dataTransfer.files]);
  };

  return (
    <section className="knowledge" aria-labelledby="knowledge-title">
      <h3 className="label sub" id="knowledge-title">
        Knowledge
      </h3>
      <p className="hint tight">Files only this agent draws on. It cites them by name and page.</p>

      <label
        className={`dropzone${over ? " over" : ""}`}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes("Files")) {
            e.preventDefault();
            setOver(true);
          }
        }}
        onDragLeave={() => setOver(false)}
        onDrop={drop}
      >
        <input
          ref={picker}
          type="file"
          multiple
          accept={ACCEPT}
          className="sr-only"
          onChange={(e) => {
            void add([...(e.target.files ?? [])]);
            e.target.value = "";
          }}
        />
        <b>{uploading ? "Adding…" : "Drop files here or choose files"}</b>
        <small>PDF, Word, text or Markdown</small>
      </label>

      {base && base.documents.length > 0 && (
        <ul className="documents">
          {base.documents.map((d) => (
            <li key={d.id} className={`document ${d.status}`}>
              <div className="document-head">
                <span className="document-name" title={d.filename}>
                  {d.filename}
                </span>
                <button type="button" className="icon-button" aria-label={`Remove ${d.filename}`} onClick={() => removeFile(d.id)}>
                  ×
                </button>
              </div>
              <span className="document-status">{documentStatus(d)}</span>
              {d.status === "indexing" && d.chunks_total > 0 && (
                <span className="progress" aria-hidden="true">
                  <i style={{ width: `${Math.round(((d.chunks_mapped + d.chunks_failed) / Math.min(d.chunks_total, MAX_GRAPH_PASSAGES)) * 100)}%` }} />
                </span>
              )}
              {d.notes.map((note) => (
                <span key={note} className="document-note">
                  {note}
                </span>
              ))}
            </li>
          ))}
        </ul>
      )}

      {base && base.documents.length > 0 && (
        <>
          <p className="hint tight knowledge-stats">
            {base.entities} entities · {base.facts} facts · {base.passages} passages · meaning search:{" "}
            {base.embedding ? base.embedding.model : "off (words only)"}
          </p>
          <div className="option-add">
            <label className="sr-only" htmlFor={`ask-${base.id}`}>
              Try a question
            </label>
            <input
              id={`ask-${base.id}`}
              value={question}
              placeholder="Try: what would this agent be told about…"
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void ask();
                }
              }}
            />
            <button type="button" className="button small" onClick={ask} disabled={!question.trim() || searching}>
              {searching ? "Looking…" : "Try"}
            </button>
          </div>
          {found && (
            <div className="found" aria-live="polite">
              {found.facts.length === 0 && found.passages.length === 0 ? (
                <p className="hint tight">Nothing in these files on that.</p>
              ) : (
                <ul>
                  {found.facts.map((f) => (
                    <li key={f.statement}>
                      {f.statement} <span className="cite">[{f.source}]</span>
                    </li>
                  ))}
                  {found.passages.map((p, i) => (
                    <li key={i} className="passage">
                      <span className="cite">[{p.source}]</span> “{p.text}”
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {confirming ? (
            <div className="history-actions confirm">
              <span>Delete these files and their graph for good?</span>
              <button type="button" className="link-button danger" onClick={deleteAll}>
                Delete
              </button>
              <button type="button" className="link-button" onClick={() => setConfirming(false)}>
                Keep
              </button>
            </div>
          ) : (
            <div className="history-actions">
              <button type="button" className="link-button quiet" onClick={() => setConfirming(true)}>
                Delete all files
              </button>
            </div>
          )}
        </>
      )}
      {!model && <p className="hint tight">Choose the discussion's model first: it maps the files into the graph.</p>}
      {problem && (
        <p className="problem" role="alert">
          {problem}
        </p>
      )}
    </section>
  );
}
