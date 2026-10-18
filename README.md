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

## Run the server

Double-click `serve.bat` (or run `uv run moot serve --open`). Moot starts on this computer at
http://127.0.0.1:8765 and opens in your browser.

Set up the discussion in the team builder: write the topic, seat agents from the library (or
make your own), edit each one on its card, choose the model, who moderates, the rules and the
round plan. Or press "Team Designer", describe your goal ("four different views on
immigration", "choose between Lisbon, Berlin or staying remote") and let it propose the panel,
the format and the options; a second pass checks the views are distinct and fair. Review the
cards, change anything, then start. To make a decision, tick "This is a decision between options", list the options
and use the Decision format: the vote asks which option to choose and the conclusion gives the
decision, why, the risks, when to revisit it and the next steps.

Then watch the round table: the speaker glows, beams show challenges, support and questions,
polls appear over the table, and the moderator closes with a conclusion. Type to join in: `@`
asks an agent (autocomplete included), `/` gives commands. When you moderate, Moot tells you
whose move it is.

Discussions are saved on this computer as they run (in `%LOCALAPPDATA%\Moot\moot.db` on
Windows; set `MOOT_DATA_DIR` in `.env` to keep them elsewhere). The team builder lists them:
open one, take its setup to run it again, or delete it. Export a discussion from its screen as
a readable report (Markdown) or as everything (JSON).

Switch to "Map & alliances" to see the argument map (every point by speaker and round, and
how points support, challenge or answer each other) and who backs whom, live; the round slider
shows both as they were after any round.

The full API is documented at http://127.0.0.1:8765/docs (and a plain test page is at /dev).
Close the window (or press Ctrl+C) to stop it. Changing the app itself: see
`frontend/README.md`.

The server only answers this computer and refuses requests from other websites, since it has
no login and uses your API keys.

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
