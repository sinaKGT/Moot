// Setting up tools: which web search is set up, and the MCP servers whose tools agents can use.

import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type { McpServerConfig, ToolsOverview } from "../../types";

interface Props {
  overview: ToolsOverview | null;
  onChanged: () => void;
  onClose: () => void;
}

const lines = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);

export function parseEnv(text: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const line of lines(text)) {
    const at = line.indexOf("=");
    if (at > 0) env[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return env;
}

export function ToolsDialog({ overview, onChanged, onClose }: Props) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"command" | "url">("command");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  const [env, setEnv] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const escape = (event: KeyboardEvent) => event.key === "Escape" && close.current();
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, []);

  async function add() {
    setProblem(null);
    const config: McpServerConfig = kind === "url" ? { url: url.trim() } : { command: command.trim(), args: lines(args), env: parseEnv(env) };
    setBusy("add");
    try {
      const info = await api.putMcp(name.trim(), config);
      if (info.status !== "connected") setProblem(`${info.name} was saved but didn't connect: ${info.error ?? info.status}`);
      else {
        setName("");
        setCommand("");
        setArgs("");
        setEnv("");
        setUrl("");
      }
      onChanged();
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function act(server: string, what: "reconnect" | "remove") {
    setBusy(`${what}:${server}`);
    setProblem(null);
    try {
      if (what === "remove") await api.deleteMcp(server);
      else await api.reconnectMcp(server);
      onChanged();
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const search = overview?.built_in.find((t) => t.id === "web_search");
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <section className="dialog panel" role="dialog" aria-modal="true" aria-labelledby="tools-dialog-title">
        <div className="dialog-head">
          <h2 id="tools-dialog-title">Tools</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>

        <h3 className="label sub">Built in</h3>
        <ul className="tool-status">
          <li>
            <b>Search the web</b> {search?.available ? <span className="ok">uses {search.note}</span> : <span className="bad">not set up</span>}
            {!search?.available && <p className="hint tight">{search?.note}</p>}
          </li>
          <li>
            <b>Read a web page</b> <span className="ok">ready</span>
            <p className="hint tight">Pages on this computer or your local network are never opened.</p>
          </li>
          <li>
            <b>Calculator</b> <span className="ok">ready</span>
          </li>
        </ul>

        <h3 className="label sub">MCP servers</h3>
        {overview?.mcp.length ? (
          <ul className="servers">
            {overview.mcp.map((server) => (
              <li key={server.name}>
                <div className="server-head">
                  <b>{server.name}</b>
                  <span className={server.status === "connected" ? "ok" : "bad"}>{server.status}</span>
                  <span className="server-actions">
                    <button type="button" className="link-button" disabled={!!busy} onClick={() => act(server.name, "reconnect")}>
                      {busy === `reconnect:${server.name}` ? "Connecting…" : "Reconnect"}
                    </button>
                    <button type="button" className="link-button quiet" disabled={!!busy} onClick={() => act(server.name, "remove")}>
                      Remove
                    </button>
                  </span>
                </div>
                <code className="server-target">{server.url ?? [server.command, ...server.args].join(" ")}</code>
                {server.env_names.length > 0 && <small className="hint">Variables: {server.env_names.join(", ")}</small>}
                {server.error && <p className="problem">{server.error}</p>}
                {server.tools.length > 0 && (
                  <p className="hint tight">
                    {server.tools.length} tool{server.tools.length === 1 ? "" : "s"}: {server.tools.map((t) => t.name.split(".").slice(1).join(".")).join(", ")}
                  </p>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="hint tight">None yet. An MCP server gives agents more tools: your files, a database, an app you use.</p>
        )}

        <details className="advanced" open={!overview?.mcp.length}>
          <summary>Add an MCP server</summary>
          <div className="row">
            <div className="field">
              <label htmlFor="mcp-name">Name</label>
              <input id="mcp-name" value={name} placeholder="e.g. files" maxLength={40} onChange={(e) => setName(e.target.value.toLowerCase())} />
            </div>
            <fieldset className="field">
              <legend>It is</legend>
              <div className="choice">
                <label>
                  <input type="radio" checked={kind === "command"} onChange={() => setKind("command")} /> A command
                </label>
                <label>
                  <input type="radio" checked={kind === "url"} onChange={() => setKind("url")} /> A URL
                </label>
              </div>
            </fieldset>
          </div>
          {kind === "command" ? (
            <>
              <div className="field">
                <label htmlFor="mcp-command">Command</label>
                <input id="mcp-command" value={command} placeholder="npx" onChange={(e) => setCommand(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="mcp-args">Arguments, one per line</label>
                <textarea id="mcp-args" rows={3} value={args} placeholder={"-y\n@modelcontextprotocol/server-filesystem\nD:\\Documents"} onChange={(e) => setArgs(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="mcp-env">Environment variables, KEY=value per line (optional)</label>
                <textarea id="mcp-env" rows={2} value={env} onChange={(e) => setEnv(e.target.value)} />
              </div>
              <p className="hint tight">Moot starts this program on your computer and keeps it running. Its settings are saved in mcp.json next to your discussions.</p>
            </>
          ) : (
            <div className="field">
              <label htmlFor="mcp-url">URL (Streamable HTTP)</label>
              <input id="mcp-url" value={url} placeholder="https://example.com/mcp" onChange={(e) => setUrl(e.target.value)} />
            </div>
          )}
          <div className="dialog-actions">
            <button
              type="button"
              className="button primary"
              disabled={!!busy || !name.trim() || (kind === "url" ? !url.trim() : !command.trim())}
              onClick={add}
            >
              {busy === "add" ? "Connecting…" : "Add and connect"}
            </button>
          </div>
        </details>
        {problem && (
          <p className="problem" role="alert">
            {problem}
          </p>
        )}
      </section>
    </div>
  );
}
