"""Prompt building: what each agent is told before it speaks.

System prompt: who the agent is, the setting, how to take part, and the rules.
User prompt: a short memory of earlier points, the most recent messages, the agent's
briefing (private notes plus what needs its attention), the round instruction, and how
to write its private notes.
"""

from __future__ import annotations

from moot.core import (
    ChatMessage,
    HumanModerator,
    Persona,
    Round,
    SessionConfig,
    SessionState,
    SpeakerKind,
    Traits,
)
from moot.memory import NOTES_INSTRUCTIONS, build_briefing, earlier_points
from moot.providers import Message

RECENT_MESSAGES = 6
MAX_EARLIER_POINTS = 12

HOW_TO_TAKE_PART = (
    "Argue your own view. When you disagree, say why with a reason, an example or a "
    "counter-argument; don't just ask for evidence or say more research is needed.",
    "Respond to what others actually said, and refer to them by name.",
    "Answer questions put to you directly.",
    "Don't repeat points you have already made.",
    "Don't drift into agreement unless you are genuinely persuaded; if you change your mind, "
    "say what changed it.",
)


def describe_traits(traits: Traits) -> list[str]:
    """Turn the behaviour sliders into plain instructions."""
    lines = []
    if traits.persuadability < 0.35:
        lines.append(
            "You hold your position firmly and change your mind only for a genuinely strong "
            "argument."
        )
    elif traits.persuadability > 0.65:
        lines.append(
            "You are open to persuasion and change your view when others make good points."
        )
    else:
        lines.append(
            "You can be persuaded by good arguments, but you don't give up your position easily."
        )

    if traits.verbosity < 0.35:
        lines.append("Keep your answers brief.")
    elif traits.verbosity > 0.65:
        lines.append("Give detailed, well-developed answers.")
    else:
        lines.append("Answer at moderate length.")

    if traits.alliance_openness < 0.35:
        lines.append("You prefer to stand alone rather than team up with others.")
    elif traits.alliance_openness > 0.65:
        lines.append("You look for allies among those who share your views.")
    return lines


def moderator_label(config: SessionConfig) -> str:
    if isinstance(config.moderator, HumanModerator):
        return f"{config.moderator.display_name} ({config.moderator.role})"
    return config.moderator.name


def participant_label(agent: Persona) -> str:
    return f"{agent.name} ({agent.role})" if agent.role else agent.name


def speaker_name(config: SessionConfig, message: ChatMessage) -> str:
    if message.speaker_kind is SpeakerKind.AGENT:
        return config.agent(message.speaker_id).name
    if message.speaker_kind is SpeakerKind.MODERATOR:
        return config.moderator_name
    if message.speaker_kind is SpeakerKind.USER:
        return "User"
    return "System"


def persona_prompt(config: SessionConfig, agent: Persona) -> str:
    """The system prompt: who the agent is, the setting, how to take part, and the rules."""
    lines: list[str] = []
    if agent.prompt_override:
        lines.append(agent.prompt_override.strip())
    else:
        lines.append(
            f"You are {participant_label(agent)}, a participant in a moderated discussion."
        )
        for label, value in (
            ("Your viewpoint", agent.viewpoint),
            ("What you want from this discussion", agent.motivation),
            ("Your expertise", agent.expertise),
            ("How you argue", agent.thinking_style),
        ):
            if value.strip():
                lines.append(f"{label}: {value.strip()}")
        lines += describe_traits(agent.traits)

    others = [participant_label(a) for a in config.agents if a.id != agent.id]
    lines += ["", f"Topic: {config.topic}"]
    if config.background.strip():
        lines.append(f"Background: {config.background.strip()}")
    lines.append(f"Moderator: {moderator_label(config)}")
    lines.append(f"Other participants: {', '.join(others)}")
    lines += ["", "How to take part:"]
    lines += [f"- {rule}" for rule in HOW_TO_TAKE_PART]
    lines += ["", "Rules you must follow:"]
    lines += [f"- {rule}" for rule in config.rules.all_rules()]
    lines += [
        "",
        f"Speak only as {agent.name}. Never write lines for other participants or the "
        "moderator, and do not start your reply with your name.",
    ]
    return "\n".join(lines)


def visible_messages(
    state: SessionState, agent: Persona, round_index: int, current: Round
) -> list[ChatMessage]:
    """What this agent can see: everything except system notices and, in a blind round,
    the other participants' answers from the current round."""
    return [
        m
        for m in state.messages
        if m.speaker_kind is not SpeakerKind.SYSTEM
        and not (current.blind and m.round_index == round_index and m.speaker_id != agent.id)
    ]


def turn_prompt(
    state: SessionState, agent: Persona, round_index: int, current: Round, turn: int
) -> str:
    """The user message: memory of the discussion, briefing, and what to do now."""
    config = state.config
    hidden_round = round_index if current.blind else None
    visible = visible_messages(state, agent, round_index, current)
    older, recent = visible[:-RECENT_MESSAGES], visible[-RECENT_MESSAGES:]

    lines: list[str] = []
    if older:
        points = earlier_points(state, {m.id for m in older}, agent, hidden_round)
        if points:
            lines.append("Earlier in the discussion:")
            lines += [f"- {point}" for point in points[-MAX_EARLIER_POINTS:]]
        else:  # the argument graph hasn't caught up yet: fall back to shortened messages
            lines.append("Earlier in the discussion:")
            lines += [
                f"- {speaker_name(config, m)}: {m.text[:200]}" for m in older[-MAX_EARLIER_POINTS:]
            ]
        lines += ["", "Recent messages:"]
    elif recent:
        lines.append("Discussion so far:")
    else:
        lines.append("Nobody has spoken yet.")
    for message in recent:
        lines += ["", f"{speaker_name(config, message)}: {message.text}"]

    briefing = build_briefing(state, agent, hidden_round)
    if briefing:
        lines += ["", briefing]

    lines += ["", f"Round {round_index + 1} of {len(config.round_plan)}: {current.name}"]
    if current.blind:
        lines.append(
            "This round is blind: you cannot see the other participants' answers in it, "
            "and they cannot see yours until it ends."
        )
    if current.turns_per_agent > 1:
        lines.append(f"This is your turn {turn + 1} of {current.turns_per_agent} in this round.")
    lines.append(f"Instruction: {current.instruction}")
    limit = current.max_words or config.rules.max_words_per_turn
    if limit:
        lines.append(f"Use at most {limit} words (not counting your private notes).")
    lines.append(f"It is your turn, {agent.name}.")
    lines += ["", NOTES_INSTRUCTIONS]
    return "\n".join(lines)


def build_agent_messages(
    state: SessionState, agent: Persona, round_index: int, current: Round, turn: int
) -> list[Message]:
    return [
        Message("system", persona_prompt(state.config, agent)),
        Message("user", turn_prompt(state, agent, round_index, current, turn)),
    ]
