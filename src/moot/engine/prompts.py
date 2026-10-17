"""Prompt building: what each agent is told before it speaks.

This is the simple version for the engine core. Step 6 adds the briefing built from the
argument graph and the agent's private notebook.
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
from moot.providers import Message


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
    """The system prompt: who the agent is, the setting, and the rules."""
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
    """The user message: the discussion so far, then what to do now."""
    config = state.config
    lines: list[str] = []
    visible = visible_messages(state, agent, round_index, current)
    if visible:
        lines.append("Discussion so far:")
        for message in visible:
            lines += ["", f"{speaker_name(config, message)}: {message.text}"]
    else:
        lines.append("Nobody has spoken yet.")

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
        lines.append(f"Use at most {limit} words.")
    lines.append(f"It is your turn, {agent.name}. Reply with your contribution only.")
    return "\n".join(lines)


def build_agent_messages(
    state: SessionState, agent: Persona, round_index: int, current: Round, turn: int
) -> list[Message]:
    return [
        Message("system", persona_prompt(state.config, agent)),
        Message("user", turn_prompt(state, agent, round_index, current, turn)),
    ]
