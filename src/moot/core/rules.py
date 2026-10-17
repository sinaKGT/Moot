"""Discussion rules: global rules for every agent, plus per-session presets and custom rules."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator

from moot.core.base import MootModel

GLOBAL_RULES: tuple[str, ...] = (
    "Never invent facts, figures, quotes or sources.",
    "Say clearly when you are unsure.",
    "Represent other participants' views accurately.",
)


class RulePreset(StrEnum):
    ANSWER_FIRST = "answer_first"
    ONE_POINT = "one_point"
    SHORT_ANSWERS = "short_answers"
    EVIDENCE_REQUIRED = "evidence_required"
    NO_REPETITION = "no_repetition"
    STAY_ON_TOPIC = "stay_on_topic"
    FORMAL_TONE = "formal_tone"


RULE_PRESET_TEXT: dict[RulePreset, str] = {
    RulePreset.ANSWER_FIRST: (
        "Start with a direct answer (Yes, No or I don't know, when the question allows it), "
        "then explain briefly if needed."
    ),
    RulePreset.ONE_POINT: (
        "Make one main point per turn and develop it, instead of listing several."
    ),
    RulePreset.SHORT_ANSWERS: "Keep answers short and to the point.",
    RulePreset.EVIDENCE_REQUIRED: "Support your claims with evidence or clear reasoning.",
    RulePreset.NO_REPETITION: "Do not repeat points that have already been made.",
    RulePreset.STAY_ON_TOPIC: "Stay on the topic under discussion.",
    RulePreset.FORMAL_TONE: "Use a formal, professional tone.",
}


DEFAULT_PRESETS: tuple[RulePreset, ...] = (RulePreset.ANSWER_FIRST, RulePreset.ONE_POINT)


class SessionRules(MootModel):
    presets: list[RulePreset] = Field(default_factory=lambda: list(DEFAULT_PRESETS))
    custom: list[str] = Field(default_factory=list)
    max_words_per_turn: int | None = Field(default=None, ge=10, le=2000)

    @field_validator("presets")
    @classmethod
    def _unique_presets(cls, value: list[RulePreset]) -> list[RulePreset]:
        return list(dict.fromkeys(value))

    @field_validator("custom")
    @classmethod
    def _clean_custom(cls, value: list[str]) -> list[str]:
        return [rule.strip() for rule in value if rule.strip()]

    def all_rules(self) -> list[str]:
        """Every rule an agent must follow, in the order they appear in its prompt."""
        rules = list(GLOBAL_RULES)
        rules += [RULE_PRESET_TEXT[preset] for preset in self.presets]
        rules += self.custom
        if self.max_words_per_turn:
            rules.append(f"Use at most {self.max_words_per_turn} words per turn.")
        return rules
