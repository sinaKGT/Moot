"""Voting: each agent picks one option in a poll, with no text.

One short structured call per agent. Agents vote at the same time, so nobody sees the
others' votes in the same poll before choosing.
"""

from __future__ import annotations

import re
import string

from pydantic import BaseModel, ConfigDict, field_validator

from moot.core import Persona, Poll, PollKind, PollOption, PollSpec, Round, SessionState
from moot.engine.prompts import discussion_lines, persona_prompt
from moot.providers import Message, extract_json_object

LETTERS = string.ascii_uppercase


class VoteChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    choice: str

    @field_validator("choice", mode="before")
    @classmethod
    def _text(cls, value: object) -> object:
        return str(value) if isinstance(value, int | float) else value


def vote_messages(
    state: SessionState,
    agent: Persona,
    poll: Poll,
    allowed: list[PollOption],
    round_index: int,
    current: Round,
) -> list[Message]:
    asker = state.config.speaker_label(poll.opened_by)
    options = [f"{LETTERS[i]}) {option.label}" for i, option in enumerate(allowed)]
    lines = discussion_lines(state, agent, round_index, current)
    lines += ["", f'{asker} has opened a poll: "{poll.question}"', "Choose exactly one option:"]
    lines += options
    if poll.kind is PollKind.WINNER:
        lines.append("(You cannot vote for yourself.)")
    lines += [
        "",
        f"Vote as {agent.name}, honestly, based on the discussion so far. Give no explanation: "
        'reply with only {"choice": "A"}, using the letter of your option.',
    ]
    return [
        Message("system", persona_prompt(state.config, agent)),
        Message("user", "\n".join(lines)),
    ]


def retry_message(allowed: list[PollOption]) -> Message:
    letters = ", ".join(LETTERS[i] for i in range(len(allowed)))
    return Message(
        "user",
        f'That is not one of the options. Reply with only {{"choice": "<letter>"}}, '
        f"where the letter is one of: {letters}.",
    )


def match_choice(text: str, allowed: list[PollOption]) -> PollOption | None:
    """The option a vote means: a letter, a number, the label or the option id."""
    cleaned = text.strip().strip("\"'`*.()[] ").removeprefix("@")
    if not cleaned:
        return None
    folded = cleaned.casefold()
    for option in allowed:  # an exact label or id wins over a letter
        if folded in (option.label.casefold(), option.id.casefold()):
            return option
    if len(cleaned) == 1 and cleaned.isalpha():
        index = LETTERS.find(cleaned.upper())
        return allowed[index] if index < len(allowed) else None
    if cleaned.isdigit():
        number = int(cleaned)
        return allowed[number - 1] if 1 <= number <= len(allowed) else None
    labelled = re.match(r"^([A-Za-z])[).:]\s*(.*)$", cleaned)  # "B) Cleo" or "B."
    if labelled:
        return match_choice(labelled.group(2) or labelled.group(1), allowed)
    return None


def parse_poll_spec(text: str) -> PollSpec:
    """Read a poll the moderator wrote: {"question": ..., "options": [...]} or
    {"winner": true}. Raises ValueError if it isn't a valid poll."""
    data = extract_json_object(text)
    question = str(data.get("question") or "").strip()
    options = data.get("options") or []
    if not isinstance(options, list):
        raise ValueError("options must be a list")
    if data.get("winner") or (not options and not question):
        spec = {"kind": PollKind.WINNER}
        if question:
            spec["question"] = question
        return PollSpec.model_validate({"question": "Who made the strongest case?", **spec})
    return PollSpec(question=question, options=[str(o) for o in options])
