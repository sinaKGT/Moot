"""Exporting a discussion: a readable report (Markdown) or everything (JSON).

The report has the conclusion, final positions, polls, alliances, who backed whom, the
argument map in words, the transcript round by round, a per-turn log and the setup. The JSON
holds the full state and the events, so nothing is lost.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable

from moot import __version__
from moot.core import AllianceStatus, SessionState
from moot.core.alliances import Alliance, AllianceStep
from moot.core.base import utc_now
from moot.core.events import EVENT_ADAPTER, Event
from moot.core.messages import SpeakerKind
from moot.core.moderator import HumanModerator
from moot.memory.alignment import pair_alignment
from moot.memory.summary import argument_map
from moot.storage.store import saved_events

EXPORT_FORMAT = 1
VERDICT_WORDS = {"yes": "Yes", "no": "No", "unsure": "Unsure"}
ORDER_WORDS = {"fixed": "fixed order", "moderator": "moderator picks", "free": "any order"}


def export_filename(state: SessionState, extension: str) -> str:
    """moot-2026-10-18-should-cities-ban-private-cars.md"""
    text = unicodedata.normalize("NFKD", state.config.topic).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(slug) > 48:  # cut at a word boundary
        slug = slug[:49].rsplit("-", 1)[0]
    slug = slug or "discussion"
    return f"moot-{state.created_at:%Y-%m-%d}-{slug}.{extension}"


def export_json(state: SessionState, events: Iterable[Event]) -> str:
    """Everything: the full state and the saved events, as one JSON document."""
    document = {
        "moot_export": EXPORT_FORMAT,
        "moot_version": __version__,
        "exported_at": utc_now().isoformat(),
        "session": state.model_dump(mode="json"),
        "events": [json.loads(EVENT_ADAPTER.dump_json(event)) for event in saved_events(events)],
    }
    return json.dumps(document, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------- Markdown


def _amount(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:.1f}"


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _quote(text: str) -> list[str]:
    return [f"> {line}" if line else ">" for line in text.strip().splitlines()]


def export_markdown(state: SessionState) -> str:
    config = state.config
    lines: list[str] = [f"# {config.topic}", ""]

    status = {
        "completed": "Finished",
        "stopped": "Stopped before the end",
        "error": "Ended by an error",
    }.get(state.end_reason or "", state.status.value.capitalize())
    moderator = config.moderator
    moderator_line = (
        f"{moderator.display_name} ({moderator.role}, a person)"
        if isinstance(moderator, HumanModerator)
        else f"{config.moderator_name} (AI)"
    )
    panel = ", ".join(f"{a.name} ({a.role})" if a.role else a.name for a in config.agents)
    lines += [
        f"{state.created_at:%d %B %Y, %H:%M} UTC · {status} · {len(config.round_plan)} rounds",
        "",
        f"**Moderator:** {moderator_line}  ",
        f"**Panel:** {panel}",
        "",
    ]
    if config.background.strip():
        lines += ["**Background:**", "", *_quote(config.background), ""]
    if config.is_decision:
        lines += ["**Options:**", ""]
        lines += [f"{chr(65 + i)}. {option}" for i, option in enumerate(config.options)]
        lines.append("")

    if state.report:
        report = state.report
        if state.audience_question:
            cut = report.lower().rfind("question for the audience:")
            report = report[:cut].rstrip(" *_\n") if cut >= 0 else report
        lines += ["## Conclusion", "", report.strip(), ""]
        if state.audience_question:
            lines += [f"**Question for the audience:** {state.audience_question}", ""]

    lines += _positions(state)
    lines += _polls(state)
    lines += _alliances(state)
    lines += _backing(state)

    # final positions are listed above, from the notebooks
    sections = [(t, items) for t, items in argument_map(state).sections() if t != "Final positions"]
    if sections:
        lines += ["## Argument map", ""]
        for title, items in sections:
            lines += [f"### {title}", "", *[f"- {item}" for item in items], ""]

    lines += _transcript(state)
    lines += _turn_log(state)
    lines += _setup(state)
    lines += ["---", f"Exported from Moot {__version__} on {utc_now():%d %B %Y}.", ""]
    return "\n".join(lines)


def _positions(state: SessionState) -> list[str]:
    config = state.config
    rows = []
    for agent in config.agents:
        notebook = state.notebooks.get(agent.id)
        if notebook is None or not (notebook.verdict or notebook.choice or notebook.stance):
            continue
        answer = notebook.choice or VERDICT_WORDS.get(notebook.verdict or "", "")
        confidence = f" ({round(notebook.confidence * 100)}%)" if answer else ""
        stance = f" — {notebook.stance}" if notebook.stance else ""
        rows.append(f"- **{agent.name}:** {answer}{confidence}{stance}")
    return ["## Final positions", "", *rows, ""] if rows else []


def _polls(state: SessionState) -> list[str]:
    name = state.config.speaker_label
    if not state.polls:
        return []
    lines = ["## Polls", ""]
    for poll in state.polls:
        when = f" (round {poll.round_index + 1})" if poll.round_index is not None else ""
        lines += [
            f"### {poll.question}{when}",
            "",
            "| Option | Votes | Voted by |",
            "|---|---|---|",
        ]
        for result in poll.results():
            voters = ", ".join(name(v) for v in result.voters) or "—"
            votes = f"{result.votes} ({_amount(result.percent)}%)"
            lines.append(f"| {_cell(result.label)} | {votes} | {voters} |")
        lines.append("")
    return lines


def _step_line(step: AllianceStep, first: bool, alliance: Alliance, name) -> str:
    who = name(step.agent_id) if step.agent_id else ""
    reason = f": “{step.reason}”" if step.reason else ""
    targets = ", ".join(name(t) for t in step.targets)
    if step.change == "proposed":
        goal = f": “{alliance.goal}”" if alliance.goal and first else ""
        return f"{who} offered {targets} an alliance{goal}" if first else f"{who} invited {targets}"
    return {
        "accepted": f"{who} accepted",
        "refused": f"{who} refused{reason}",
        "left": f"{who} left{reason}",
        "dissolved": "The alliance ended",
    }[step.change]


def _alliances(state: SessionState) -> list[str]:
    name = state.config.speaker_label
    if not state.alliances:
        return []
    lines = ["## Alliances", ""]
    for alliance in state.alliances:
        members = ", ".join(name(m) for m in alliance.members) or "nobody yet"
        status = {
            AllianceStatus.ACTIVE: "active",
            AllianceStatus.PROPOSED: "offered",
            AllianceStatus.DISSOLVED: "ended",
        }[alliance.status]
        title = f"“{alliance.goal}”" if alliance.goal else "Alliance"
        lines += [f"- **{title}** — {status}; members: {members}"]
        for i, step in enumerate(alliance.history):
            when = f"R{step.round_index + 1}" if step.round_index is not None else "–"
            lines.append(f"  - {when}: {_step_line(step, i == 0, alliance, name)}")
    lines.append("")
    return lines


def _backing(state: SessionState) -> list[str]:
    name = state.config.speaker_label
    rows = []
    for pair in pair_alignment(state):
        if not pair.interactions and not pair.allied:
            continue
        score = pair.score
        words = (
            "no exchanges"
            if not pair.interactions
            else "backed each other"
            if score >= 0.34
            else "opposed"
            if score <= -0.34
            else "mixed"
        )
        counts = ", ".join(
            f"{label} {_amount(value)}"
            for label, value in (("support", pair.support), ("challenge", pair.challenge))
            if value
        )
        allied = "; allied" if pair.allied else ""
        detail = f" ({counts})" if counts else ""
        rows.append(f"- {name(pair.first)} – {name(pair.second)}: {words}{detail}{allied}")
    return ["## Who backed whom", "", *rows, ""] if rows else []


def _transcript(state: SessionState) -> list[str]:
    config = state.config
    name = config.speaker_label
    rounds = config.round_plan.rounds
    by_id = {m.id: m for m in state.messages}
    lines = ["## Transcript", ""]
    current: object = object()
    rounds_begun = False
    for message in state.messages:
        if message.round_index != current:
            current = message.round_index
            if message.round_index is None:
                lines += ["### After the rounds" if rounds_begun else "### Before round 1", ""]
            else:
                rounds_begun = True
                index = message.round_index
                title = rounds[index].name if index < len(rounds) else ""
                lines += [f"### Round {index + 1}" + (f" · {title}" if title else ""), ""]
        if message.speaker_kind is SpeakerKind.SYSTEM:
            lines += [f"*{message.text.strip()}*", ""]
            continue
        asked = by_id.get(message.reply_to) if message.reply_to else None
        to = f" → {name(asked.speaker_id)}" if asked else ""
        lines += [f"**{name(message.speaker_id)}**{to}: {message.text.strip()}", ""]
    return lines if state.messages else []


def _turn_log(state: SessionState) -> list[str]:
    name = state.config.speaker_label
    turns = [m for m in state.messages if m.usage or m.model or m.seconds]
    if not turns:
        return []
    lines = [
        "## Turn log",
        "",
        "| # | Speaker | Round | Model | Tokens in | Tokens out | Seconds |",
        "|---|---|---|---|---|---|---|",
    ]
    total_in = total_out = 0
    for i, m in enumerate(turns, start=1):
        tokens_in = m.usage.prompt_tokens if m.usage else None
        tokens_out = m.usage.completion_tokens if m.usage else None
        total_in += tokens_in or 0
        total_out += tokens_out or 0
        round_label = str(m.round_index + 1) if m.round_index is not None else "–"
        lines.append(
            f"| {i} | {name(m.speaker_id)} | {round_label} | {m.model or '–'} | "
            f"{tokens_in if tokens_in is not None else '–'} | "
            f"{tokens_out if tokens_out is not None else '–'} | "
            f"{m.seconds if m.seconds is not None else '–'} |"
        )
    lines += ["", f"Total: {total_in} tokens in, {total_out} tokens out.", ""]
    return lines


def _setup(state: SessionState) -> list[str]:
    config = state.config
    lines = ["## Setup", "", "### Panel", ""]
    for agent in config.agents:
        model = config.model_for(agent)
        parts = [f"**{agent.name}**" + (f" — {agent.role}" if agent.role else "")]
        for label, value in (
            ("Viewpoint", agent.viewpoint),
            ("Motivation", agent.motivation),
            ("Expertise", agent.expertise),
            ("Thinking style", agent.thinking_style),
        ):
            if value.strip():
                parts.append(f"{label}: {value.strip()}")
        parts.append(f"Model: {model.provider}/{model.model}" if model else "Model: —")
        lines.append("- " + "  \n  ".join(parts))
    lines += ["", "### Rules", "", *[f"- {rule}" for rule in config.rules.all_rules()], ""]
    lines += ["### Round plan", ""]
    for i, round_ in enumerate(config.round_plan.rounds, start=1):
        notes = [ORDER_WORDS.get(round_.speaking_order.value, round_.speaking_order.value)]
        if round_.blind:
            notes.insert(0, "blind")
        if round_.turns_per_agent > 1:
            notes.append(f"{round_.turns_per_agent} turns each")
        if round_.mentions_allowed:
            notes.append("@mentions allowed")
        if round_.max_words:
            notes.append(f"at most {round_.max_words} words")
        instruction = f" {round_.instruction}" if round_.instruction else ""
        lines.append(f"{i}. **{round_.name}** ({', '.join(notes)}).{instruction}")
    lines.append("")
    return lines
