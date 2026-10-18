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

## Watch a debate

Double-click `watch-debate.bat`, type a topic and pick a model. Ava argues for it, Ben
against, and Cleo starts undecided, over an opening, a challenge and a closing round,
then they vote on who made the strongest case. The moderator closes with a conclusion and
a question for the audience. Agents can form alliances; the end of the run shows the
argument map and who backed whom.

You can join in while they talk: type a message and press Enter. Write `@Ava` or `@Ben`
to ask one of them directly; they answer right away. In the challenge round the agents
can also @ each other. Commands:

- `/poll` asks everyone who made the strongest case
- `/poll Question | option | option` runs your own poll (`/poll Question` alone offers
  Yes / No / Not sure)
- `/pause` and `/resume`

When it asks who moderates, choose **You** to run the discussion yourself under your own
name and role. Moot then waits for you: `/next` starts each round, `/floor Ben` gives
someone the floor in the challenge round (`/floor` alone lets Moot choose), `/end` ends a
round, `/remind Ben keep it short` reminds an agent, and `/finish` ends the discussion.
`@all` asks every agent at once. There is no AI moderator help in this mode.

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
