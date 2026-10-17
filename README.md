# Moot

A local-first platform where a team of AI agents discusses a topic under a moderator.
Build the team, give each agent its own persona, model, knowledge and tools, define the
rounds and rules, then let an AI moderator run the discussion or moderate it yourself.

**Status:** pre-alpha, under construction. See [docs/v1-features.md](docs/v1-features.md)
for the v1 spec and build order.

## Development setup

Requires [uv](https://docs.astral.sh/uv/). Everything installs into the project's own
`.venv`; nothing is installed globally.

```powershell
uv sync                  # create .venv and install dev tools
uv run moot --version    # check the command works
uv run pytest            # run the tests
uv run ruff check .      # lint
```

## Live check

Copy `.env.example` to `.env` and add your OpenRouter key, then double-click
`try-moot.bat`. It checks OpenRouter, and Ollama / LM Studio if they are running.

## Project layout

```
src/moot/
  core/        data model: personas, rules, round plan, events
  providers/   connections to local and cloud models
  engine/      discussion loop, moderator, alliances, polls
  memory/      argument graph, private notebooks, agent briefings
  knowledge/   per-agent files (RAG)
  tools/       built-in tools and MCP
  server/      web API and live event stream; serves the UI
tests/         automated tests
scripts/       developer scripts (live check)
docs/          spec and design notes
```
