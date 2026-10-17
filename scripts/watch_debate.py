"""Watch two agents debate a topic. Double-click `watch-debate.bat` to run it.

Asks for a topic and a model (OpenRouter with the key in `.env`, or a model running in
LM Studio or Ollama), then streams the discussion.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from moot.core import ModelRef, SessionState
from moot.demo import DEFAULT_OPENROUTER_MODEL, DEMO_TOPIC, demo_config, run_in_console
from moot.envfile import load_env_file
from moot.providers import ProviderError, create_provider

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def local_models(preset_id: str) -> list[str]:
    """Chat models available in a local server, or [] if it isn't running."""
    async with create_provider(preset_id) as provider:
        try:
            models = await provider.list_models()
        except ProviderError:
            return []
    return [m.id for m in models if "embed" not in m.id.lower()]


def model_choices() -> list[tuple[str, ModelRef]]:
    choices = []
    if os.environ.get("OPENROUTER_API_KEY"):
        model = os.environ.get("MOOT_TEST_MODEL") or DEFAULT_OPENROUTER_MODEL
        choices.append((f"OpenRouter: {model}", ModelRef(provider="openrouter", model=model)))
    for preset_id, label in (("lmstudio", "LM Studio"), ("ollama", "Ollama")):
        for model in asyncio.run(local_models(preset_id)):
            choices.append((f"{label}: {model}", ModelRef(provider=preset_id, model=model)))
    return choices


def ask_choice(choices: list[tuple[str, ModelRef]]) -> ModelRef:
    print("Which model should the agents use?")
    for number, (label, _) in enumerate(choices, start=1):
        print(f"  [{number}] {label}")
    while True:
        answer = input("Number (press Enter for 1): ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1][1]
        print("Please type one of the numbers above.")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")

    load_env_file(PROJECT_ROOT / ".env")
    print("Moot - watch a debate\n")

    topic = input(f"Topic (press Enter for: {DEMO_TOPIC})\n> ").strip() or DEMO_TOPIC
    print("\nLooking for models...")
    choices = model_choices()
    if not choices:
        print(
            "No model found. Add OPENROUTER_API_KEY to the .env file, or start LM Studio or "
            "Ollama with a model loaded."
        )
        return 1
    model = ask_choice(choices)
    print()

    state = SessionState.new(demo_config(topic, model))
    try:
        reason = asyncio.run(run_in_console(state))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 1
    return 0 if reason == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
