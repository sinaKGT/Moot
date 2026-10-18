// Loads a session and follows its live event stream.

import { useEffect, useReducer, useRef, useState } from "react";
import { api, eventsUrl } from "../api";
import { EVENT_TYPES, type MootEvent, type SessionView } from "../types";
import { initialState, reduce, type DiscussionState, type Names } from "./reducer";

export function speakerNames(view: SessionView): Names {
  const names: Names = { moderator: view.summary.moderator, user: "You", system: "System" };
  for (const agent of view.state.config.agents) names[agent.id] = agent.name;
  return names;
}

export function useSession(id: string) {
  const [view, setView] = useState<SessionView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const names = useRef<Names>({});
  const [state, dispatch] = useReducer(
    (current: DiscussionState, event: MootEvent) => reduce(current, event, names.current),
    initialState,
  );

  useEffect(() => {
    let source: EventSource | null = null;
    let cancelled = false;
    api
      .session(id)
      .then((loaded) => {
        if (cancelled) return;
        names.current = speakerNames(loaded);
        setView(loaded);
        source = new EventSource(eventsUrl(id));
        for (const type of EVENT_TYPES) {
          source.addEventListener(type, (message) => {
            const event = JSON.parse((message as MessageEvent<string>).data) as MootEvent;
            dispatch(event);
            if (event.type === "session_ended") source?.close(); // or the browser reconnects
          });
        }
      })
      .catch((problem: Error) => !cancelled && setError(problem.message));
    return () => {
      cancelled = true;
      source?.close();
    };
  }, [id]);

  return { view, state, names: names.current, error };
}
