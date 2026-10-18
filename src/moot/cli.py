"""Command-line entry point for the `moot` command."""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import webbrowser
from collections.abc import Sequence
from pathlib import Path

from moot import __version__
from moot.core import ModelRef, SessionState
from moot.demo import DEFAULT_OPENROUTER_MODEL, DEMO_TOPIC, demo_config, run_in_console
from moot.envfile import load_env_file
from moot.providers import (
    PRESETS,
    ChatRequest,
    Message,
    ProviderError,
    TextDelta,
    Usage,
    create_provider,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moot",
        description="A local-first platform for moderated multi-agent discussions.",
    )
    parser.add_argument("--version", action="store_true", help="show the version and exit")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    commands.add_parser("providers", help="list the model providers Moot can use")

    models = commands.add_parser("models", help="list the models a provider offers")
    models.add_argument("provider", help="provider id, e.g. ollama, lmstudio, openrouter")
    models.add_argument("--base-url", help="use a different address for the provider")

    ask = commands.add_parser("ask", help="send one prompt to a model and stream the answer")
    ask.add_argument("provider", help="provider id, e.g. ollama, lmstudio, openrouter")
    ask.add_argument("model", help="model id, as shown by `moot models`")
    ask.add_argument("prompt", help="what to ask")
    ask.add_argument("--system", help="optional system prompt")
    ask.add_argument("--temperature", type=float, help="sampling temperature, e.g. 0.7")
    ask.add_argument("--base-url", help="use a different address for the provider")

    debate = commands.add_parser("debate", help="watch two agents debate a topic (short demo)")
    debate.add_argument("--topic", default=DEMO_TOPIC, help="what to debate")
    debate.add_argument("--provider", default="openrouter", help="provider id (default openrouter)")
    debate.add_argument("--model", help="model id (OpenRouter defaults to a free model)")
    debate.add_argument("--no-color", action="store_true", help="plain text output")

    serve = commands.add_parser("serve", help="start the Moot server on this computer")
    serve.add_argument("--host", default=DEFAULT_HOST, help=f"address (default {DEFAULT_HOST})")
    serve.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})"
    )
    serve.add_argument("--open", action="store_true", help="open it in your browser")
    serve.add_argument(
        "--data-dir",
        type=Path,
        help="where discussions are saved (default: MOOT_DATA_DIR, or the usual app data folder)",
    )

    return parser


def load_settings(data_dir: Path | None = None) -> list[Path]:
    """Read `.env` from the current folder, then from Moot's data folder (where an installed
    Moot keeps its settings). Variables already set win; returns the files read."""
    from moot.storage import default_data_dir

    read = []
    for path in (Path.cwd() / ".env", (data_dir or default_data_dir()) / ".env"):
        if path.exists() and path not in read:
            load_env_file(path)
            read.append(path)
    return read


def main(argv: Sequence[str] | None = None) -> int:
    _tolerate_unprintable_characters()
    args = build_parser().parse_args(argv)

    if args.version:
        print(f"moot {__version__}")
        return 0
    if args.command is None:  # plain `moot`: start it and open it
        args = build_parser().parse_args(["serve", "--open"])
    load_settings(getattr(args, "data_dir", None))
    if args.command == "providers":
        return _cmd_providers()
    if args.command == "models":
        return asyncio.run(_cmd_models(args))
    if args.command == "ask":
        return asyncio.run(_cmd_ask(args))
    if args.command == "debate":
        return _cmd_debate(args)
    return _cmd_serve(args)


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from moot.knowledge import KNOWLEDGE_DB, KnowledgeError, KnowledgeStore
    from moot.server import LOCAL_HOSTS, SessionManager, create_app
    from moot.storage import DB_NAME, SessionStore, StoreError, default_data_dir
    from moot.tools import MCP_FILE, McpHub, ToolBox

    data_dir = args.data_dir or default_data_dir()
    shown_host = f"[{args.host}]" if ":" in args.host else args.host
    url = f"http://{shown_host}:{args.port}"
    running = _already_running(args.host, args.port)
    if running is not None:
        if running:
            print(f"Moot is already running at {url}.")
            if args.open:
                webbrowser.open(url)
            return 0
        print(
            f"Port {args.port} is used by another program. Start Moot on another port, "
            f"e.g. `moot serve --open --port {args.port + 1}`.",
            file=sys.stderr,
        )
        return 1
    try:
        store = SessionStore(data_dir / DB_NAME)
        knowledge = KnowledgeStore(data_dir / KNOWLEDGE_DB)
    except (StoreError, KnowledgeError, OSError) as exc:
        print(f"Moot can't open its saved data: {exc}", file=sys.stderr)
        return 1
    local = args.host in LOCAL_HOSTS
    tools = ToolBox(McpHub(data_dir / MCP_FILE))
    manager = SessionManager(store=store, knowledge=knowledge, tools=tools)
    app = create_app(manager, allowed_hosts=LOCAL_HOSTS if local else None)
    print(f"Moot {__version__} is running at {url}  (API docs: {url}/docs)")
    print(f"Discussions are saved in {data_dir}")
    if not local:
        print(
            "Warning: anyone who can reach this address can use Moot, with your API keys. "
            "Use the default address unless you mean to share it."
        )
    print("Press Ctrl+C to stop.")
    if args.open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _already_running(host: str, port: int) -> bool | None:
    """None if the port is free; True if a Moot answers there; False if something else does."""
    import socket

    import httpx

    probe = "127.0.0.1" if host in ("0.0.0.0", "", "localhost") else host
    try:
        with socket.create_connection((probe, port), timeout=0.5):
            pass
    except OSError:
        return None
    shown = f"[{probe}]" if ":" in probe else probe
    try:
        answer = httpx.get(f"http://{shown}:{port}/api/health", timeout=2.0)
        return (
            answer.status_code == 200
            and answer.json().get("status") == "ok"
            and "version" in answer.json()
        )
    except (httpx.HTTPError, ValueError):
        return False


def _cmd_providers() -> int:
    print(f"{'ID':<12}{'NAME':<28}{'ADDRESS':<58}API KEY")
    for preset in PRESETS.values():
        address = preset.base_url or "set with --base-url"
        if preset.api_key_env is None:
            key = "not needed"
        elif preset.local:
            key = f"{preset.api_key_env} (optional)"
        else:
            key = preset.api_key_env
        print(f"{preset.id:<12}{preset.label:<28}{address:<58}{key}")
    return 0


async def _cmd_models(args: argparse.Namespace) -> int:
    try:
        provider = create_provider(args.provider, base_url=args.base_url)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    async with provider:
        try:
            models = await provider.list_models()
        except ProviderError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    if not models:
        print("No models found. Download a model in your provider first.")
        return 0
    for model in models:
        suffix = f"  ({model.context_length:,} tokens)" if model.context_length else ""
        print(f"{model.id}{suffix}")
    return 0


async def _cmd_ask(args: argparse.Namespace) -> int:
    try:
        provider = create_provider(args.provider, base_url=args.base_url)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    messages: list[Message] = []
    if args.system:
        messages.append(Message("system", args.system))
    messages.append(Message("user", args.prompt))
    request = ChatRequest(model=args.model, messages=messages, temperature=args.temperature)

    usage: Usage | None = None
    wrote_text = False
    async with provider:
        try:
            async for event in provider.stream(request):
                if isinstance(event, TextDelta):
                    sys.stdout.write(event.text)
                    sys.stdout.flush()
                    wrote_text = True
                else:
                    usage = event.usage
        except ProviderError as exc:
            if wrote_text:
                print(flush=True)
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    print(flush=True)
    if usage:
        print(f"[tokens: {usage.prompt_tokens} in, {usage.completion_tokens} out]", file=sys.stderr)
    return 0


def _cmd_debate(args: argparse.Namespace) -> int:
    if args.provider not in PRESETS:
        print(f"Error: unknown provider '{args.provider}'.", file=sys.stderr)
        return 2
    model = args.model or (DEFAULT_OPENROUTER_MODEL if args.provider == "openrouter" else None)
    if model is None:
        print(f"Error: choose a model for {args.provider} with --model.", file=sys.stderr)
        return 2

    state = SessionState.new(demo_config(args.topic, ModelRef(provider=args.provider, model=model)))
    try:
        reason = asyncio.run(run_in_console(state, color=False if args.no_color else None))
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    return 0 if reason == "completed" else 1


def _tolerate_unprintable_characters() -> None:
    """Avoid crashes on consoles that can't display every character a model writes."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass
