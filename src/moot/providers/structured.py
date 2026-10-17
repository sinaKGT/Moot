"""Structured answers: ask a model for JSON, read it robustly and check it against a schema."""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel

from moot.providers.base import ChatRequest, Message, Provider

T = TypeVar("T", bound=BaseModel)

_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class StructuredOutputError(Exception):
    """The model did not return valid JSON for the requested schema."""


def extract_json_object(text: str) -> dict:
    """Find the first JSON object in a model's answer.

    Handles answers wrapped in code fences, preceded by explanations, or containing a
    <think>...</think> block from reasoning models.
    """
    cleaned = _THINKING.sub("", text)
    if "</think>" in cleaned.lower():  # an opening tag was cut off: keep only what follows
        cleaned = cleaned[cleaned.lower().rindex("</think>") + len("</think>") :]
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned, index)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON object found in the answer")


async def complete_structured(
    provider: Provider, request: ChatRequest, schema: type[T], *, retries: int = 1
) -> T:
    """Get an answer matching `schema`, retrying with the error message if it doesn't."""
    messages = list(request.messages)
    problem = "no answer"
    for _attempt in range(retries + 1):
        result = await provider.complete(
            ChatRequest(
                model=request.model,
                messages=messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                json_mode=True,
            )
        )
        try:
            return schema.model_validate(extract_json_object(result.text))
        except ValueError as exc:  # includes pydantic's ValidationError
            problem = str(exc).splitlines()[0][:300]
            messages = [
                *request.messages,
                Message("assistant", result.text[:4000]),
                Message(
                    "user",
                    f"That answer could not be used ({problem}). "
                    "Reply again with only the JSON object, in the shape described.",
                ),
            ]
    raise StructuredOutputError(f"no valid JSON after {retries + 1} attempts: {problem}")
