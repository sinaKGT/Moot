"""Live check: test Moot against real model providers with one script.

Double-click `try-moot.bat` in the project folder to run it. Keys are read from the
`.env` file in the project folder, which git ignores.

Checks:
  1. OpenRouter: the key works and a model streams an answer
  2. Ollama: if running with a model downloaded, it streams an answer
  3. LM Studio: same as Ollama
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from moot import __version__
from moot.envfile import load_env_file
from moot.providers import (
    ChatRequest,
    Message,
    ModelNotFoundError,
    OpenAICompatibleProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    TextDelta,
    create_provider,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Free OpenRouter models, tried in order (free models are sometimes busy).
OPENROUTER_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
]

PROMPT = "In one short sentence, say hello and name one thing a good debate moderator does."


@dataclass
class Result:
    name: str
    status: str  # PASS, SKIP or FAIL
    detail: str


async def stream_answer(provider: OpenAICompatibleProvider, model: str) -> tuple[str, str]:
    """Stream a short answer to the console; return the text and a token summary."""
    request = ChatRequest(model=model, messages=[Message("user", PROMPT)], max_tokens=200)
    print(f"   {model}:")
    parts: list[str] = []
    tokens = "token count not reported"
    try:
        async for event in provider.stream(request):
            if isinstance(event, TextDelta):
                if not parts:
                    print("   > ", end="", flush=True)
                print(event.text, end="", flush=True)
                parts.append(event.text)
            elif event.usage:
                tokens = (
                    f"{event.usage.prompt_tokens} in / {event.usage.completion_tokens} out tokens"
                )
    finally:
        if parts:
            print()
    return "".join(parts), tokens


async def check_openrouter() -> Result:
    name = "OpenRouter"
    if not os.environ.get("OPENROUTER_API_KEY"):
        return Result(name, "FAIL", "no OPENROUTER_API_KEY in the .env file")

    preferred = os.environ.get("MOOT_TEST_MODEL")
    models = ([preferred] if preferred else []) + OPENROUTER_MODELS

    async with create_provider("openrouter") as provider:
        try:
            available = {model.id for model in await provider.list_models()}
        except ProviderError as exc:
            return Result(name, "FAIL", str(exc))
        print(f"   Connected: {len(available)} models available.")

        candidates = [model for model in models if model in available] or models
        last_problem = "no model answered"
        for model in candidates:
            try:
                text, tokens = await stream_answer(provider, model)
            except ProviderAuthError as exc:
                return Result(name, "FAIL", str(exc))
            except (ModelNotFoundError, ProviderRateLimitError, ProviderResponseError) as exc:
                print(f"   (skipping: {exc})")
                last_problem = str(exc)
                continue
            except ProviderError as exc:
                return Result(name, "FAIL", str(exc))
            if text.strip():
                return Result(name, "PASS", f"{model}, {tokens}")
            last_problem = f"{model} returned an empty answer"
            print(f"   ({last_problem}, trying the next model)")
        return Result(name, "FAIL", last_problem)


async def check_local(preset_id: str, name: str) -> Result:
    async with create_provider(preset_id) as provider:
        try:
            models = await provider.list_models()
        except ProviderConnectionError:
            return Result(name, "SKIP", "not running")
        except ProviderError as exc:
            return Result(name, "FAIL", str(exc))

        chat_models = [model for model in models if "embed" not in model.id.lower()]
        if not chat_models:
            return Result(name, "SKIP", "running, but no chat model downloaded yet")

        model = chat_models[0].id
        print(f"   Connected: {len(chat_models)} model(s) found. Loading may take a moment.")
        try:
            text, tokens = await stream_answer(provider, model)
        except ProviderError as exc:
            return Result(name, "FAIL", str(exc))
        if not text.strip():
            return Result(name, "FAIL", f"{model} returned an empty answer")
        return Result(name, "PASS", f"{model}, {tokens}")


async def run_checks() -> list[Result]:
    results = []
    for number, (label, check) in enumerate(
        (
            ("OpenRouter", check_openrouter()),
            ("Ollama", check_local("ollama", "Ollama")),
            ("LM Studio", check_local("lmstudio", "LM Studio")),
        ),
        start=1,
    ):
        print(f"\n{number}. {label}")
        result = await check
        print(f"   [{result.status}] {result.detail}")
        results.append(result)
    return results


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")

    load_env_file(PROJECT_ROOT / ".env")
    print(f"Moot {__version__} - live check")

    results = asyncio.run(run_checks())

    print("\nSummary")
    for result in results:
        print(f"  [{result.status}] {result.name}: {result.detail}")

    failed = any(result.status == "FAIL" for result in results)
    print("\nSomething failed - see the messages above." if failed else "\nAll good.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
