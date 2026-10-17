"""Find @mentions of agents in a message, without asking a model."""

from __future__ import annotations

import re
from collections.abc import Sequence

from moot.core import Persona

EVERYONE = re.compile(r"(?<![\w@])@(all|everyone)(?!\w)", re.IGNORECASE)


def find_mentions(text: str, agents: Sequence[Persona], *, exclude: str | None = None) -> list[str]:
    """Ids of the agents mentioned as @Name, in order of first mention.

    Matching ignores case, prefers longer names ("@Domain Expert" over "@Domain"), and
    needs the @ to start a word, so e-mail addresses don't count. "@all" or "@everyone"
    mentions every agent (after anyone named explicitly).
    """
    by_length = sorted(agents, key=lambda agent: len(agent.name), reverse=True)
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for agent in by_length:
        pattern = re.compile(rf"(?<![\w@])@{re.escape(agent.name)}(?!\w)", re.IGNORECASE)
        for match in pattern.finditer(text):
            span = match.span()
            if any(start < span[1] and span[0] < end for start, end in taken):
                continue
            taken.append(span)
            found.append((span[0], agent.id))
    ordered = []
    for _, agent_id in sorted(found):
        if agent_id != exclude and agent_id not in ordered:
            ordered.append(agent_id)
    taken_names = {agent.name.casefold() for agent in agents}
    everyone = [m for m in EVERYONE.finditer(text) if m.group(1).casefold() not in taken_names]
    if everyone:
        ordered += [a.id for a in agents if a.id != exclude and a.id not in ordered]
    return ordered
