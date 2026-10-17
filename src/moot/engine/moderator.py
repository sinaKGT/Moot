"""The AI moderator: opens the discussion, picks speakers, summarises rounds and writes the
final report. It stays neutral and never argues a side.

This module builds the moderator's prompts and reads its decisions; the engine decides
when the moderator speaks.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from moot.core import (
    ChatMessage,
    ModelRef,
    Persona,
    Round,
    SessionConfig,
    SessionState,
    SpeakerKind,
)
from moot.memory import alliance_lines, argument_map, attention_lines, poll_lines
from moot.providers import ChatRequest, Message, Provider, complete_structured, extract_json_object

POLL_OFFER = (
    "Only if the participants are clearly split and a quick poll would show where everyone "
    "stands, you may also add one hidden line after your summary: "
    '<poll>{"question": "...", "options": ["...", "..."]}</poll>, or '
    '<poll>{"winner": true}</poll> to ask who made the strongest case. Usually, leave it out.'
)

OPENING_TASK = "Open the discussion."
SUMMARY_TASK = "has just ended."
SELECTION_TASK = "Choose who should speak next"
REPORT_TASK = "Write the final report"
TOPIC_TASK = "Is this topic clear enough to discuss?"

MAX_SUBQUESTIONS = 4
RECENT_FOR_MODERATOR = 8


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class NextSpeaker(_Lenient):
    next: str
    reason: str = ""


class TopicCheck(_Lenient):
    clear: bool = True
    question: str = ""


def _name(config: SessionConfig, message: ChatMessage) -> str:
    if message.speaker_kind is SpeakerKind.AGENT:
        return config.agent(message.speaker_id).name
    if message.speaker_kind is SpeakerKind.MODERATOR:
        return config.moderator_name
    return "User" if message.speaker_kind is SpeakerKind.USER else "System"


def _transcript(config: SessionConfig, messages: Sequence[ChatMessage]) -> str:
    return "\n\n".join(
        f"{_name(config, m)}: {m.text}"
        for m in messages
        if m.speaker_kind is not SpeakerKind.SYSTEM
    )


def _format(config: SessionConfig) -> str:
    return "\n".join(
        f"Round {i + 1}: {r.name}. {r.instruction}" for i, r in enumerate(config.round_plan.rounds)
    )


class ModeratorBrain:
    def system_prompt(self, config: SessionConfig) -> str:
        people = ", ".join(f"{a.name} ({a.role})" if a.role else a.name for a in config.agents)
        rules = "\n".join(f"- {rule}" for rule in config.rules.all_rules())
        background = (
            f"\nBackground: {config.background.strip()}" if config.background.strip() else ""
        )
        return (
            f"You are {config.moderator_name}, the moderator of a discussion between these "
            f"participants: {people}.\n"
            f"Topic: {config.topic}{background}\n\n"
            "You are neutral: never take a side or give your own opinion on the topic. "
            "Be brief, clear and fair, and address participants by name. Do not start your "
            "reply with your own name.\n\n"
            f"Rules the participants follow:\n{rules}"
        )

    # ----------------------------------------------------------------- opening

    def opening_messages(self, state: SessionState) -> list[Message]:
        config = state.config
        user = (
            f"{OPENING_TASK} In at most 100 words: welcome the participants by name, restate "
            f"the topic in one sentence, and list 2 to {MAX_SUBQUESTIONS} short sub-questions "
            "that will structure the discussion.\n\n"
            f"The format:\n{_format(config)}\n\n"
            "After your opening, add the sub-questions in exactly this form; it is hidden from "
            'the audience:\n<plan>{"subquestions": ["...", "..."]}</plan>'
        )
        return [Message("system", self.system_prompt(config)), Message("user", user)]

    @staticmethod
    def parse_plan(plan: str, visible_text: str = "") -> list[str]:
        """Sub-questions from the hidden plan, or from question lines in the opening."""
        questions: list[str] = []
        try:
            value = extract_json_object(plan).get("subquestions", [])
            if isinstance(value, list):
                questions = [str(q).strip() for q in value if str(q).strip()]
        except ValueError:
            questions = []
        if not questions:
            for line in visible_text.splitlines():
                cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
                if cleaned != line.strip() and cleaned.endswith("?"):
                    questions.append(cleaned)
        return list(dict.fromkeys(q[:200] for q in questions))[:MAX_SUBQUESTIONS]

    # ---------------------------------------------------------------- summaries

    def summary_messages(
        self, state: SessionState, round_index: int, *, offer_poll: bool = False
    ) -> list[Message]:
        config = state.config
        rounds = config.round_plan.rounds
        current = rounds[round_index]
        said = [m for m in state.messages if m.round_index == round_index]
        next_round = ""
        if round_index + 1 < len(rounds):
            upcoming = rounds[round_index + 1]
            next_round = (
                f"\n\nThen introduce the next round in one sentence: Round {round_index + 2}: "
                f"{upcoming.name}. {upcoming.instruction}"
            )
        alliances = alliance_lines(state)
        alliance_part = (
            "\n\nAlliances:\n" + "\n".join(alliances) + "\nMention new or broken alliances, "
            "or allies who challenge each other, if there are any."
            if alliances
            else ""
        )
        polls = poll_lines(state)
        poll_part = "\n\nPoll results so far:\n" + "\n".join(polls) if polls else ""
        user = (
            f"Round {round_index + 1}: {current.name} {SUMMARY_TASK} What was said in it:\n\n"
            f"{_transcript(config, said) or '(nothing)'}{alliance_part}{poll_part}\n\n"
            "In at most 80 words, summarise the main points and where the participants clash, "
            "and name anything someone was asked but did not answer. Stay neutral."
            f"{next_round}" + (f"\n\n{POLL_OFFER}" if offer_poll else "")
        )
        return [Message("system", self.system_prompt(config)), Message("user", user)]

    # --------------------------------------------------------- speaker choice

    def selection_messages(
        self,
        state: SessionState,
        round_index: int,
        current: Round,
        candidates: Sequence[Persona],
        turns: dict[str, int],
    ) -> list[Message]:
        config = state.config
        hidden = round_index if current.blind else None
        waiting = "\n".join(
            f"- {a.name}: {turns.get(a.id, 0)} of {current.turns_per_agent} turns used"
            for a in candidates
        )
        open_items = []
        for agent in candidates:
            items = [
                line
                for line in attention_lines(state, agent, hidden)
                if "haven't answered" not in line
            ]
            if items:
                open_items.append(f"{agent.name}:\n" + "\n".join(items))
        recent = [m for m in state.messages if m.speaker_kind is SpeakerKind.AGENT][-6:]
        user = (
            f"Round {round_index + 1}: {current.name}. Instruction: {current.instruction}\n\n"
            f"Who still has turns in this round:\n{waiting}\n\n"
            f"Open challenges and questions:\n{chr(10).join(open_items) or 'None.'}\n\n"
            f"Recent messages:\n{_transcript(config, recent) or 'None yet.'}\n\n"
            f"{SELECTION_TASK} so the discussion is fair and flows naturally: prefer someone who "
            "was just challenged or asked a question, and don't let one person dominate. "
            'Reply with only a JSON object: {"next": "Name", "reason": "a short reason"}'
        )
        return [Message("system", self.system_prompt(config)), Message("user", user)]

    async def pick_next(
        self,
        state: SessionState,
        round_index: int,
        current: Round,
        candidates: Sequence[Persona],
        turns: dict[str, int],
        provider: Provider,
        model: ModelRef,
    ) -> NextSpeaker:
        messages = self.selection_messages(state, round_index, current, candidates, turns)
        return await complete_structured(
            provider, ChatRequest(model=model.model, messages=messages, temperature=0), NextSpeaker
        )

    # ------------------------------------------------------------ final report

    def report_messages(self, state: SessionState) -> list[Message]:
        config = state.config
        material = []
        for title, items in argument_map(state).sections():
            material.append(f"{title}:\n" + "\n".join(f"- {item}" for item in items))
        history = []
        for agent in config.agents:
            notebook = state.notebooks.get(agent.id)
            if notebook and notebook.history:
                steps = ", ".join(
                    f"round {s.round_index + 1} {(s.verdict or 'undecided').upper()} "
                    f"({round(s.confidence * 100)}%)"
                    for s in notebook.history
                )
                history.append(f"- {agent.name}: {steps}")
        if history:
            material.append("Positions over time:\n" + "\n".join(history))
        alliances = alliance_lines(state)
        if alliances:
            material.append("Alliances:\n" + "\n".join(alliances))
        polls = poll_lines(state, limit=None)
        if polls:
            material.append("Polls (each agent picked one option):\n" + "\n".join(polls))
        recent = state.messages[-RECENT_FOR_MODERATOR:]
        material.append(f"Last messages:\n{_transcript(config, recent)}")
        user = (
            f"The discussion has ended. {REPORT_TASK} in at most 250 words, using these headings "
            "on their own lines:\n"
            "Answer: the overall answer or recommendation the discussion points to, or say that "
            "it stayed undecided\n"
            "Where they agree\n"
            "Where they disagree\n"
            "Final positions: each participant's final answer and whether it changed\n"
            "Open questions\n"
            + ("Alliances: who teamed up, and whether the alliances held\n" if alliances else "")
            + ("Vote: what the polls showed\n" if polls else "")
            + "Stay neutral and base everything on what was said.\n\n"
            + "\n\n".join(material)
        )
        return [Message("system", self.system_prompt(config)), Message("user", user)]


async def check_topic(
    topic: str, background: str, provider: Provider, model: ModelRef
) -> TopicCheck:
    """Before starting: ask the moderator model whether the topic is clear, or what to ask."""
    context = f"\nBackground: {background}" if background.strip() else ""
    messages = [
        Message("system", "You are the moderator preparing a discussion between AI participants."),
        Message(
            "user",
            f'Topic proposed by the user: "{topic}"{context}\n\n'
            f"{TOPIC_TASK} It is unclear if it contains a typo that changes its meaning, is "
            "ambiguous, or could reasonably mean quite different things.\n"
            'Reply with only a JSON object: {"clear": true} or '
            '{"clear": false, "question": "one short question to ask the user"}',
        ),
    ]
    return await complete_structured(
        provider, ChatRequest(model=model.model, messages=messages, temperature=0), TopicCheck
    )
