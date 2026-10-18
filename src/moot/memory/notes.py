"""Private notes: what each agent writes about its own position after every turn.

Agents add a hidden `<notes>{...}</notes>` block to their answer. It is read here and merged
into the agent's notebook; if it is missing or unreadable, the previous notes are kept.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from moot.core import Notebook, Persona, SessionConfig, SessionState
from moot.providers import extract_json_object

MAX_ITEMS = 5
MAX_ITEM_CHARS = 200

NOTES_INSTRUCTIONS = """After your reply, add your private notes in exactly this form. Nobody \
else will see them, so never mention them in your reply:
<notes>
{"verdict": "yes", "confidence": 0.7, "stance": "your position in one sentence", \
"key_arguments": ["..."], "concessions": ["points you now accept"], \
"judgements": {"Name": "how convincing their arguments are"}, "allies": ["Name"], \
"opponents": ["Name"], "strategy": "what you plan to do next", "questions_to_ask": ["..."]}
</notes>
"verdict" is your answer to the topic question: "yes", "no" or "unsure". "confidence" is \
from 0 to 1. Use the participants' names exactly as written."""

_VERDICTS = {
    "yes": "yes",
    "y": "yes",
    "agree": "yes",
    "for": "yes",
    "no": "no",
    "n": "no",
    "disagree": "no",
    "against": "no",
    "unsure": "unsure",
    "undecided": "unsure",
    "i don't know": "unsure",
    "don't know": "unsure",
    "dont know": "unsure",
    "not sure": "unsure",
    "partly": "unsure",
    "mixed": "unsure",
    "neutral": "unsure",
}


class NotesUpdate(BaseModel):
    """What an agent's notes block may contain. Missing fields leave the notebook unchanged."""

    model_config = ConfigDict(extra="ignore")

    verdict: str | None = None
    choice: str | None = None
    confidence: float | None = None
    stance: str | None = None
    key_arguments: list[str] | None = None
    concessions: list[str] | None = None
    judgements: dict[str, str] | None = None
    allies: list[str] | None = None
    opponents: list[str] | None = None
    strategy: str | None = None
    questions_to_ask: list[str] | None = None

    @field_validator("verdict", mode="before")
    @classmethod
    def _normalise_verdict(cls, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        return _VERDICTS.get(value.strip().strip(".!").lower())

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalise_confidence(cls, value: Any) -> float | None:
        if isinstance(value, str):
            value = value.strip().rstrip("%")
            try:
                value = float(value)
            except ValueError:
                return None
        if not isinstance(value, int | float):
            return None
        value = float(value)
        if 1 < value <= 100:
            value /= 100
        return min(max(value, 0.0), 1.0)

    @field_validator(
        "key_arguments", "concessions", "allies", "opponents", "questions_to_ask", mode="before"
    )
    @classmethod
    def _clean_list(cls, value: Any) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return None
        items = [" ".join(str(item).split())[:MAX_ITEM_CHARS] for item in value]
        return [item for item in items if item][:MAX_ITEMS]


def parse_notes(raw: str) -> NotesUpdate:
    """Read a notes block. Raises ValueError if it holds no usable JSON object."""
    return NotesUpdate.model_validate(extract_json_object(raw))


def split_trailing_notes(text: str) -> tuple[str, str]:
    """For models that forget the <notes> tags: move a trailing JSON object that looks like
    notes out of the visible text. Returns (visible text, notes text)."""
    for index in range(len(text) - 1, -1, -1):
        if text[index] != "{":
            continue
        tail = text[index:].strip().rstrip("`").strip()
        try:
            value = json.loads(tail)
        except ValueError:
            continue
        if isinstance(value, dict) and ("verdict" in value or "stance" in value):
            visible = text[:index].rstrip().removesuffix("```json").removesuffix("```").rstrip()
            return visible, tail
        break
    return text, ""


DECISION_NOTES = (
    'This is a decision, so also add "choice": the option you favour now, named exactly as '
    "listed (or a new option you propose)."
)


def notes_instructions(config: SessionConfig) -> str:
    """How to write private notes; decisions also ask for the option the agent favours."""
    if not config.is_decision:
        return NOTES_INSTRUCTIONS
    return f"{NOTES_INSTRUCTIONS}\n{DECISION_NOTES}"


def match_option(text: str, options: list[str]) -> str:
    """The listed option an agent means ("hybrid" -> "Hybrid"), or its own words."""
    cleaned = " ".join(text.split()).strip(" .\"'")[:MAX_ITEM_CHARS]
    folded = cleaned.casefold()
    for option in options:
        if option.casefold() == folded:
            return option
    starts = [
        o for o in options if o.casefold().startswith(folded) or folded.startswith(o.casefold())
    ]
    return starts[0] if len(starts) == 1 else cleaned


def apply_notes(
    state: SessionState, agent: Persona, update: NotesUpdate, round_index: int
) -> Notebook:
    """Merge the update into the agent's notebook and record the stance for this round."""
    config = state.config
    notebook = state.notebooks[agent.id]

    def ids(names: list[str]) -> list[str]:
        found = [config.agent_by_name(name) for name in names]
        return list(dict.fromkeys(a.id for a in found if a and a.id != agent.id))

    if update.verdict is not None:
        notebook.verdict = update.verdict  # type: ignore[assignment]
    if update.choice and update.choice.strip():
        notebook.choice = match_option(update.choice, config.options)
    if update.confidence is not None:
        notebook.confidence = update.confidence
    if update.stance:
        notebook.stance = " ".join(update.stance.split())[: MAX_ITEM_CHARS * 2]
    if update.strategy is not None:
        notebook.strategy = " ".join(update.strategy.split())[: MAX_ITEM_CHARS * 2]
    for name in ("key_arguments", "concessions", "questions_to_ask"):
        value = getattr(update, name)
        if value is not None:
            setattr(notebook, name, value)
    if update.allies is not None:
        notebook.allies = ids(update.allies)
    if update.opponents is not None:
        notebook.opponents = ids(update.opponents)
    if update.judgements is not None:
        judgements = {}
        for name, judgement in update.judgements.items():
            other = config.agent_by_name(name)
            if other and other.id != agent.id and str(judgement).strip():
                judgements[other.id] = " ".join(str(judgement).split())[:MAX_ITEM_CHARS]
        notebook.judgements = judgements
    notebook.snapshot(round_index)
    return notebook
