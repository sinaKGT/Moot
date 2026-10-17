"""Checks of the rules that can be measured without a model."""

from __future__ import annotations

import re

from moot.core import RULE_PRESET_TEXT, Round, RulePreset, SessionConfig

WORD_LIMIT_TOLERANCE = 1.15  # don't nag about a few extra words

_YES_NO_TOPIC = re.compile(
    r"^\s*(is|are|was|were|do|does|did|should|shall|can|could|will|would|has|have|had|must|may)\b",
    re.IGNORECASE,
)
_ANSWER_START = re.compile(
    r"^[\W_]*(yes|no|i don['’]t know|i do not know|not sure|unsure|partly|partially|"
    r"it depends|maybe|absolutely|definitely|not really|not necessarily)\b",
    re.IGNORECASE,
)


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def is_yes_no_topic(topic: str) -> bool:
    return bool(_YES_NO_TOPIC.match(topic))


def check_rules(config: SessionConfig, current: Round, text: str) -> list[str]:
    """The rules an answer clearly broke, worded as reminders."""
    broken = []
    limit = current.max_words or config.rules.max_words_per_turn
    if limit and word_count(text) > limit * WORD_LIMIT_TOLERANCE:
        broken.append(f"Use at most {limit} words.")
    if (
        RulePreset.ANSWER_FIRST in config.rules.presets
        and is_yes_no_topic(config.topic)
        and not _ANSWER_START.match(text)
    ):
        broken.append(RULE_PRESET_TEXT[RulePreset.ANSWER_FIRST])
    return broken
