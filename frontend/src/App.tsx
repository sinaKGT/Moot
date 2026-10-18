import { useEffect, useState } from "react";
import { SessionScreen } from "./components/SessionScreen";
import { StartScreen } from "./components/StartScreen";

const sessionInUrl = () => new URLSearchParams(window.location.search).get("session");

export function App() {
  const [sessionId, setSessionId] = useState<string | null>(sessionInUrl);

  useEffect(() => {
    const onBack = () => setSessionId(sessionInUrl());
    window.addEventListener("popstate", onBack);
    return () => window.removeEventListener("popstate", onBack);
  }, []);

  const open = (id: string | null) => {
    window.history.pushState(null, "", id ? `?session=${id}` : "/");
    setSessionId(id);
  };

  return sessionId ? (
    <SessionScreen key={sessionId} id={sessionId} onLeave={() => open(null)} />
  ) : (
    <StartScreen onOpen={open} />
  );
}
