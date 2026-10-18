// Choosing a model: where it runs (provider), then which model. Lists are fetched once per provider.

import { useEffect, useState } from "react";
import { api } from "../../api";
import type { ModelEntry, ModelRef, ProviderInfo } from "../../types";

export const PREFERRED_MODEL = "nvidia/nemotron-3-super-120b-a12b:free";

const lists = new Map<string, Promise<ModelEntry[]>>();

/** Free models first, then by name. */
function sorted(models: ModelEntry[]): ModelEntry[] {
  return [...models].sort(
    (a, b) => Number(b.id.endsWith(":free")) - Number(a.id.endsWith(":free")) || a.id.localeCompare(b.id),
  );
}

export function loadModels(provider: string): Promise<ModelEntry[]> {
  let list = lists.get(provider);
  if (!list) {
    list = api.models(provider).then(sorted);
    list.catch(() => lists.delete(provider)); // try again next time
    lists.set(provider, list);
  }
  return list;
}

export function usableProvider(providers: ProviderInfo[]): ProviderInfo | undefined {
  return providers.find((p) => p.key_set) ?? providers.find((p) => p.local) ?? providers[0];
}

export const pickModel = (models: ModelEntry[]) => models.find((m) => m.id === PREFERRED_MODEL)?.id ?? models[0]?.id;

interface Props {
  id: string;
  providers: ProviderInfo[];
  value: ModelRef | null;
  onChange: (model: ModelRef | null) => void;
}

export function ModelPicker({ id, providers, value, onChange }: Props) {
  const [provider, setProvider] = useState(value?.provider ?? usableProvider(providers)?.id ?? "");
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!provider && providers.length) setProvider(usableProvider(providers)?.id ?? "");
  }, [providers, provider]);

  useEffect(() => {
    // follow changes from outside, such as the draft being reset
    if (value?.provider && value.provider !== provider) setProvider(value.provider);
  }, [value?.provider]);

  useEffect(() => {
    if (!provider) return;
    let current = true;
    setModels([]);
    setNote("Looking for models…");
    loadModels(provider)
      .then((found) => {
        if (!current) return;
        setModels(found);
        setNote(found.length ? `${found.length} model${found.length === 1 ? "" : "s"}` : "No models found. Is a model loaded?");
        // keep the current choice if this provider still lists it, else take its best model
        const keep = value?.provider === provider && found.some((m) => m.id === value.model);
        if (!keep) {
          const model = pickModel(found);
          if (model) onChange({ provider, model });
          else if (value && value.provider !== provider) onChange(null);
        }
      })
      .catch((e: Error) => current && setNote(`Couldn't list models: ${e.message}`));
    return () => {
      current = false;
    };
  }, [provider]); // value and onChange are read when the provider changes, not followed

  const listed = models.some((m) => m.id === value?.model);
  return (
    <div className="model-picker">
      <div className="row">
        <div className="field">
          <label htmlFor={`${id}-provider`}>Runs on</label>
          <select id={`${id}-provider`} value={provider} onChange={(e) => setProvider(e.target.value)}>
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
          <label htmlFor={`${id}-model`}>Model</label>
          <select
            id={`${id}-model`}
            value={value?.provider === provider ? value.model : ""}
            disabled={!models.length && !value}
            onChange={(e) => onChange({ provider, model: e.target.value })}
          >
            {value && value.provider === provider && !listed && <option value={value.model}>{value.model}</option>}
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className="hint" aria-live="polite">
        {note}
      </p>
    </div>
  );
}

/** Embedding models first (their ids usually say "embed"). */
export const looksLikeEmbedding = (id: string) => /embed|bge|e5-|gte-|minilm/i.test(id);

const LOCAL_FIRST = ["lmstudio", "ollama"];

function embeddingProvider(providers: ProviderInfo[]): ProviderInfo | undefined {
  for (const id of LOCAL_FIRST) {
    const found = providers.find((p) => p.id === id);
    if (found) return found;
  }
  return usableProvider(providers);
}

interface EmbeddingProps {
  providers: ProviderInfo[];
  value: ModelRef | null;
  auto: boolean;
  onChange: (model: ModelRef | null, byHand: boolean) => void;
}

/** The model for meaning search in agents' files: a local embedding model if one is there,
 * else none (search by words only). Chosen automatically until chosen by hand. */
export function EmbeddingPicker({ providers, value, auto, onChange }: EmbeddingProps) {
  const [provider, setProvider] = useState(value?.provider ?? "");
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!provider && providers.length) setProvider(embeddingProvider(providers)?.id ?? "");
  }, [providers, provider]);

  useEffect(() => {
    if (!provider) return;
    let current = true;
    setModels([]);
    setNote("Looking for embedding models…");
    loadModels(provider)
      .then((found) => {
        if (!current) return;
        const ordered = [...found].sort((a, b) => Number(looksLikeEmbedding(b.id)) - Number(looksLikeEmbedding(a.id)));
        setModels(ordered);
        const embedders = ordered.filter((m) => looksLikeEmbedding(m.id));
        setNote(embedders.length ? "" : "No embedding model found here; agents' files are searched by their words.");
        if (auto && !value && embedders[0]) onChange({ provider, model: embedders[0].id }, false);
      })
      .catch(() => current && setNote("Couldn't list models here; agents' files are searched by their words."));
    return () => {
      current = false;
    };
  }, [provider]); // value, auto and onChange are read when the provider changes

  return (
    <div className="model-picker">
      <div className="row">
        <div className="field">
          <label htmlFor="embedding-provider">Runs on</label>
          <select id="embedding-provider" value={provider} onChange={(e) => setProvider(e.target.value)}>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
                {p.local ? " (this computer)" : ""}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="embedding-model">Embedding model</label>
          <select
            id="embedding-model"
            value={value && value.provider === provider ? value.model : ""}
            onChange={(e) => onChange(e.target.value ? { provider, model: e.target.value } : null, true)}
          >
            <option value="">None: search by words</option>
            {value && value.provider === provider && !models.some((m) => m.id === value.model) && <option value={value.model}>{value.model}</option>}
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
              </option>
            ))}
          </select>
        </div>
      </div>
      {note && <p className="hint">{note}</p>}
    </div>
  );
}
