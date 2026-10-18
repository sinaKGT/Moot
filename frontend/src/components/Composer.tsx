// The input bar: say something, @ to ask an agent, / for a command. Suggestions as you type.

import { useRef, useState, type KeyboardEvent } from "react";
import { suggestions, type Suggestion } from "../commands";
import type { Persona } from "../types";

interface Props {
  agents: Persona[];
  human: boolean;
  disabled: boolean;
  onSubmit: (text: string) => Promise<void>;
}

export function Composer({ agents, human, disabled, onSubmit }: Props) {
  const [text, setText] = useState("");
  const [caret, setCaret] = useState(0);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const names = agents.map((a) => a.name);
  const options = dismissed || disabled ? [] : suggestions(text, caret, names, human);
  const open = options.length > 0;
  const current = Math.min(active, Math.max(options.length - 1, 0));

  const place = (value: string, position: number) => {
    setText(value);
    setCaret(position);
    setActive(0);
    requestAnimationFrame(() => {
      input.current?.focus();
      input.current?.setSelectionRange(position, position);
    });
  };

  const accept = (s: Suggestion) => place(text.slice(0, s.from) + s.insert + text.slice(s.to), s.from + s.insert.length);

  const insertAtCaret = (snippet: string) => {
    const before = text.slice(0, caret);
    const spacer = before && !/\s$/.test(before) ? " " : "";
    place(before + spacer + snippet + text.slice(caret), caret + spacer.length + snippet.length);
  };

  async function send() {
    if (!text.trim() || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await onSubmit(text);
      place("", 0);
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      setActive((current + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length);
    } else if (open && (event.key === "Enter" || event.key === "Tab")) {
      event.preventDefault();
      accept(options[current]);
    } else if (open && event.key === "Escape") {
      setDismissed(true);
    } else if (event.key === "Enter") {
      event.preventDefault();
      void send();
    }
  }

  const syncCaret = () => setCaret(input.current?.selectionStart ?? text.length);

  return (
    <>
      <div className="composer">
        {open && (
          <ul id="moot-suggest" className="suggest" role="listbox" aria-label="Suggestions">
            {options.map((option, index) => (
              <li
                key={option.label}
                id={`moot-suggest-${index}`}
                role="option"
                aria-selected={index === current}
                onMouseDown={(e) => {
                  e.preventDefault();
                  accept(option);
                }}
              >
                <span>{option.label}</span>
                {option.detail && <span className="detail">{option.detail}</span>}
              </li>
            ))}
          </ul>
        )}
        <label htmlFor="say" className="sr-only">
          Message to the discussion
        </label>
        <input
          id="say"
          ref={input}
          value={text}
          disabled={disabled}
          autoComplete="off"
          placeholder={disabled ? "The discussion has finished." : "Say something — @ to ask an agent, / for a command"}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls="moot-suggest"
          aria-activedescendant={open ? `moot-suggest-${current}` : undefined}
          onChange={(e) => {
            setText(e.target.value);
            setCaret(e.target.selectionStart ?? e.target.value.length);
            setDismissed(false);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          onKeyUp={syncCaret}
          onClick={syncCaret}
        />
        <div className="quick">
          {agents.map((agent) => (
            <button key={agent.id} type="button" disabled={disabled} style={{ color: agent.color, borderColor: `${agent.color}66` }} onClick={() => insertAtCaret(`@${agent.name} `)}>
              @{agent.name}
            </button>
          ))}
          <button type="button" disabled={disabled} onClick={() => place("/poll ", 6)}>
            /poll
          </button>
        </div>
        <button type="button" className="button primary" disabled={disabled || busy || !text.trim()} onClick={() => void send()}>
          Send
        </button>
      </div>
      {problem && (
        <p className="problem" role="alert">
          {problem}
        </p>
      )}
    </>
  );
}
