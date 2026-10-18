// An agent's tools: tick the built-in ones and those of connected MCP servers.

import type { ToolsOverview } from "../../types";

interface Props {
  overview: ToolsOverview | null;
  selected: string[];
  onChange: (tools: string[]) => void;
  onManage: () => void;
}

export function ToolsPanel({ overview, selected, onChange, onManage }: Props) {
  const toggle = (id: string, on: boolean) => onChange(on ? [...selected, id] : selected.filter((t) => t !== id));
  const known = new Set([...(overview?.built_in ?? []).map((t) => t.id), ...(overview?.mcp ?? []).flatMap((s) => s.tools.map((t) => t.id))]);
  const missing = selected.filter((id) => !known.has(id));
  return (
    <section className="tools" aria-labelledby="tools-title">
      <h3 className="label sub" id="tools-title">
        Tools
      </h3>
      <p className="hint tight">Before it speaks, it may use these to check facts. Everyone sees what it looked up.</p>
      {!overview ? (
        <p className="hint tight">Loading…</p>
      ) : (
        <>
          {overview.built_in.map((tool) => (
            <label key={tool.id} className={`check tool-check${tool.available ? "" : " off"}`} title={tool.note || tool.description}>
              <input type="checkbox" checked={selected.includes(tool.id)} disabled={!tool.available && !selected.includes(tool.id)} onChange={(e) => toggle(tool.id, e.target.checked)} />
              <span>
                {LABELS[tool.id] ?? tool.name}
                {!tool.available && <small> · not set up</small>}
                {tool.available && tool.note && <small> · {tool.note}</small>}
              </span>
            </label>
          ))}
          {overview.mcp.map((server) => (
            <div key={server.name} className="tool-group">
              <span className="tool-server">
                {server.name}
                <small className={server.status === "connected" ? "ok" : "bad"}> · {server.status}</small>
              </span>
              {server.tools.map((tool) => (
                <label key={tool.id} className="check tool-check" title={tool.description}>
                  <input type="checkbox" checked={selected.includes(tool.id)} onChange={(e) => toggle(tool.id, e.target.checked)} />
                  <span>{tool.name.split(".").slice(1).join(".") || tool.name}</span>
                </label>
              ))}
            </div>
          ))}
          {missing.length > 0 && (
            <p className="hint tight">
              Not available now: {missing.join(", ")}.{" "}
              <button type="button" className="link-button" onClick={() => onChange(selected.filter((t) => known.has(t)))}>
                Untick
              </button>
            </p>
          )}
        </>
      )}
      <button type="button" className="link-button" onClick={onManage}>
        Set up web search and MCP servers
      </button>
    </section>
  );
}

const LABELS: Record<string, string> = {
  web_search: "Search the web",
  fetch_page: "Read a web page",
  calculator: "Calculator",
};
