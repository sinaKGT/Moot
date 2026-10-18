// Editing one agent: who it is, how it argues, and (optionally) its own model or whole prompt.

import { Avatar } from "../../avatar";
import { AGENT_COLORS, type DraftAgent } from "../../builder/draft";
import type { ModelRef, ProviderInfo, Traits } from "../../types";
import { ModelPicker } from "./ModelPicker";

const COLOR_NAMES: Record<string, string> = {
  "#60a5fa": "Blue",
  "#fb7185": "Coral",
  "#a3e635": "Lime",
  "#f472b6": "Pink",
  "#facc15": "Yellow",
  "#38bdf8": "Sky",
  "#d6b98c": "Sand",
};

const SLIDERS: { trait: keyof Traits; low: string; high: string }[] = [
  { trait: "persuadability", low: "Holds position", high: "Persuadable" },
  { trait: "verbosity", low: "Concise", high: "Detailed" },
  { trait: "alliance_openness", low: "Lone wolf", high: "Open to alliances" },
];

interface Props {
  agent: DraftAgent;
  takenColors: string[];
  providers: ProviderInfo[];
  defaultModel: ModelRef | null;
  onChange: (patch: Partial<Omit<DraftAgent, "key">>) => void;
  onRemove: () => void;
  canRemove: boolean;
}

export function AgentCard({ agent, takenColors, providers, defaultModel, onChange, onRemove, canRemove }: Props) {
  const id = `agent-${agent.key}`;
  const text = (field: "role" | "viewpoint" | "motivation" | "expertise" | "thinking_style", label: string, hint: string, rows = 0) => (
    <div className="field">
      <label htmlFor={`${id}-${field}`}>{label}</label>
      {rows ? (
        <textarea id={`${id}-${field}`} rows={rows} value={agent[field]} placeholder={hint} onChange={(e) => onChange({ [field]: e.target.value })} />
      ) : (
        <input id={`${id}-${field}`} value={agent[field]} placeholder={hint} onChange={(e) => onChange({ [field]: e.target.value })} />
      )}
    </div>
  );

  return (
    <aside className="panel agent-card" aria-label={`Agent card: ${agent.name || "unnamed"}`}>
      <div className="agent-head">
        <span className="agent-avatar" style={{ borderColor: agent.color }}>
          <Avatar name={agent.name || "?"} color={agent.color} size={34} />
        </span>
        <div className="field grow">
          <label htmlFor={`${id}-name`}>Name</label>
          <input id={`${id}-name`} value={agent.name} maxLength={40} onChange={(e) => onChange({ name: e.target.value.replace(/[@\n]/g, "") })} />
        </div>
      </div>

      <div className="swatches" role="radiogroup" aria-label="Colour">
        {AGENT_COLORS.map((color) => (
          <button
            key={color}
            type="button"
            role="radio"
            aria-checked={agent.color === color}
            aria-label={`${COLOR_NAMES[color] ?? color}${takenColors.includes(color) ? " (used by another agent)" : ""}`}
            className={`swatch${agent.color === color ? " on" : ""}${takenColors.includes(color) ? " taken" : ""}`}
            style={{ background: color }}
            onClick={() => onChange({ color })}
          />
        ))}
      </div>

      {text("role", "Role", "Economist, Critic, Parent…")}
      {text("viewpoint", "Viewpoint", "What it believes and argues for", 3)}
      {text("motivation", "Motivation", "What it wants from the discussion")}
      {text("expertise", "Expertise", "What it knows well")}
      {text("thinking_style", "Thinking style", "How it argues: with data, stories, questions…")}

      <div className="sliders">
        {SLIDERS.map(({ trait, low, high }) => (
          <label key={trait} className="slider">
            <span>{low}</span>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={Math.round(agent.traits[trait] * 100)}
              aria-label={`${low} to ${high}`}
              onChange={(e) => onChange({ traits: { ...agent.traits, [trait]: Number(e.target.value) / 100 } })}
            />
            <span>{high}</span>
          </label>
        ))}
      </div>

      <label className="check">
        <input
          type="checkbox"
          checked={agent.model !== null}
          onChange={(e) => onChange({ model: e.target.checked ? defaultModel : null })}
        />
        Use its own model
      </label>
      {agent.model !== null ? (
        <ModelPicker key={agent.key} id={`${id}-model`} providers={providers} value={agent.model} onChange={(model) => onChange({ model })} />
      ) : (
        <p className="hint">Uses the discussion's model{defaultModel ? `: ${defaultModel.model}` : ""}.</p>
      )}

      <details className="advanced">
        <summary>Advanced: write its whole prompt</summary>
        <p className="hint">Replaces who it is: the fields and sliders above. The topic, rules and round instructions are still added.</p>
        <textarea
          rows={6}
          aria-label="Custom prompt"
          value={agent.prompt_override ?? ""}
          onChange={(e) => onChange({ prompt_override: e.target.value || null })}
        />
      </details>

      <button type="button" className="button danger small" onClick={onRemove} disabled={!canRemove}>
        Remove from the table
      </button>
    </aside>
  );
}
