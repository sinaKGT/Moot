"""Mapping a passage into the knowledge graph: the things it is about (entities) and the
facts that link them, each fact kept with the passage it came from.

One structured model call per passage. Entities are merged by name across passages, so the
graph connects what different parts of the files say about the same thing.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, field_validator

from moot.providers import ChatRequest, Message, Provider, complete_structured

MAX_ENTITIES = 12
MAX_FACTS = 12
MAX_NAME_CHARS = 80
MAX_TEXT_CHARS = 300

EXTRACTION_SYSTEM = f"""You build a knowledge graph from documents. From the passage, list:
- "entities": the specific things it is about (people, organisations, places, products, \
policies, concepts, events), at most {MAX_ENTITIES}. Name them fully ("Oslo city centre", not \
"centre"); skip generic words, dates and bare numbers (they belong in facts). For each: "name" \
(short, the same name every time for the same thing), "type" (one word) and "description" (one \
short sentence from the passage).
- "facts": what the passage states about them, at most {MAX_FACTS}. For each: "subject" (an \
entity name from your list), "relation" (a few words, e.g. "costs", "is located in", \
"reduced"), "object" (another entity name, or a value such as "about a sixth") and \
"statement": the fact as one sentence that makes sense on its own, read without the passage: \
name who or what it is about instead of "it", "the city" or "the centre", and keep the numbers, \
dates and conditions.

Only what the passage says: no outside knowledge, no guesses. Keep the passage's language.

Reply with only a JSON object:
{{"entities": [{{"name": "...", "type": "...", "description": "..."}}], "facts": [{{"subject": \
"...", "relation": "...", "object": "...", "statement": "..."}}]}}"""


class _Lenient(BaseModel):
    model_config = {"extra": "ignore"}


class ExtractedEntity(_Lenient):
    name: str
    type: str = ""
    description: str = ""

    @field_validator("name", "type", "description", mode="before")
    @classmethod
    def _text(cls, value: object) -> str:
        return " ".join(str(value or "").split())


class ExtractedFact(_Lenient):
    subject: str
    relation: str = ""
    object: str = ""
    statement: str = ""

    @field_validator("subject", "relation", "object", "statement", mode="before")
    @classmethod
    def _text(cls, value: object) -> str:
        return " ".join(str(value or "").split())


class Extraction(_Lenient):
    entities: list[ExtractedEntity] = []
    facts: list[ExtractedFact] = []


def entity_key(name: str) -> str:
    """How entity names are matched: case, accents, punctuation and a leading "the" ignored."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode() or name
    text = re.sub(r"[^\w\s]", " ", text.casefold())
    text = re.sub(r"^(the|a|an)\s+", "", " ".join(text.split()))
    return text.strip()


def clean_extraction(extraction: Extraction) -> Extraction:
    """Drop empty or oversized pieces; keep facts whose subject is a listed entity (the object
    may be a plain value such as "25 euros")."""
    entities: list[ExtractedEntity] = []
    seen: set[str] = set()
    for entity in extraction.entities:
        key = entity_key(entity.name)
        if not key or key in seen or len(entity.name) > MAX_NAME_CHARS:
            continue
        seen.add(key)
        entities.append(
            ExtractedEntity(
                name=entity.name,
                type=entity.type[:40],
                description=entity.description[:MAX_TEXT_CHARS],
            )
        )
        if len(entities) == MAX_ENTITIES:
            break
    facts: list[ExtractedFact] = []
    for fact in extraction.facts:
        if not fact.statement or entity_key(fact.subject) not in seen:
            continue
        facts.append(
            ExtractedFact(
                subject=fact.subject,
                relation=fact.relation[:60],
                object=fact.object[:MAX_NAME_CHARS],
                statement=fact.statement[:MAX_TEXT_CHARS],
            )
        )
        if len(facts) == MAX_FACTS:
            break
    return Extraction(entities=entities, facts=facts)


async def extract(provider: Provider, model: str, passage: str, source: str) -> Extraction:
    """The entities and facts in one passage. Raises StructuredOutputError or ProviderError."""
    messages = [
        Message("system", EXTRACTION_SYSTEM),
        Message("user", f'Passage from {source}:\n"""\n{passage}\n"""'),
    ]
    found = await complete_structured(
        provider, ChatRequest(model=model, messages=messages, temperature=0), Extraction
    )
    return clean_extraction(found)
