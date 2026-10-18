# Moot

A local-first platform where a team of AI agents discusses a topic under a moderator.
Build the team, give each agent its own persona, model, knowledge and tools, define the
rounds and rules, then let an AI moderator run the discussion, or moderate it yourself.
Everything runs on your computer; models come from your own keys or local servers.

**Status:** v1 feature-complete (all 21 steps of [docs/v1-features.md](docs/v1-features.md),
where every decision is logged).

## Install

Moot needs [uv](https://docs.astral.sh/uv/). It installs Moot in its own isolated
environment; nothing else on your computer changes.

- **Double-click `install-moot.bat`.** It installs Moot from this folder and starts it.
- Or in a terminal: `uv tool install "D:\10. AgentSwarm"`

Then start Moot any time by typing **`moot`**: it opens in your browser at
http://127.0.0.1:8765. If `moot` isn't found, run `uv tool update-shell` once and open a new
terminal. Starting it again while it runs simply opens the running one.

- **Update** after changes to this folder: double-click `install-moot.bat` again (or
  `uv tool install --reinstall "D:\10. AgentSwarm"`).
- **Uninstall:** `uv tool uninstall moot`. Your discussions and knowledge stay in the data
  folder (below) until you delete it.
- **Without installing**, from this folder: double-click `serve.bat` (`uv run moot serve --open`).

## Settings and keys

Moot reads `.env` files: first from the folder you start it in, then from its data folder
(`%LOCALAPPDATA%\Moot\.env` on Windows, `~/.local/share/moot/.env` on Linux,
`~/Library/Application Support/Moot/.env` on macOS). See `.env.example`.

| Setting | For |
|---|---|
| `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY` | cloud models (local LM Studio and Ollama need no key) |
| `TAVILY_API_KEY` (1,000 free searches a month), `BRAVE_API_KEY` or `SEARXNG_URL` | the "Search the web" tool |
| `MOOT_DATA_DIR` | keep Moot's data somewhere else |

The data folder holds `moot.db` (saved discussions), `knowledge.db` (agents' files and their
knowledge graphs) and `mcp.json` (MCP servers). Keys are never shown by the app or its API.

## Using Moot

**Set up a discussion** in the team builder: write the topic, seat agents from the library
(drag or press) or make your own, and edit each one on its card: name, colour, role,
viewpoint, motivation, expertise, thinking style, how persuadable, detailed and open to
alliances it is, optionally its own model or whole prompt. Choose the discussion's model, who
moderates (the AI, or you with your own name and role), the rules and the round plan
(templates, or your own rounds). Your last setup is remembered.

**Team Designer:** describe your goal ("four different views on immigration", "choose between
Lisbon, Berlin or staying remote") and it proposes the panel, the format and the options; a
second pass checks the views are distinct and fair. Review the cards, change anything, start.

**Decisions:** tick "This is a decision between options", list the options and use the
Decision format (options, criteria, weigh, pre-mortem, decide). The vote asks which option to
choose, and the conclusion gives the decision, why, the risks, when to revisit it and the
next steps.

**Watch and join in:** the speaker glows at the round table, beams show challenges, support
and questions, polls appear over the table, and the moderator closes with a conclusion and a
question for the audience. Type to join in: `@Name` asks an agent (autocomplete included),
`/` gives commands (`/poll`, `/pause`, `/resume`, and when you moderate `/next`, `/floor`,
`/end`, `/remind`, `/finish`). When you moderate, Moot tells you whose move it is.

**Map & alliances:** the argument map shows every point by speaker and round and how points
support, challenge or answer each other; the alliance monitor shows who backs whom and each
alliance's story. Both update live, and a slider shows them as they were after any round.

**Knowledge:** drop PDF, Word, text or Markdown files on an agent's card. Moot turns them into
that agent's own knowledge graph (the entities and facts in them, with an embedding model for
meaning search, LM Studio by default) and, before the agent speaks, briefs it privately with
what bears on the moment; it cites the file and page. "Try" on the card shows what it would
be told. Mapping a file takes one model call per passage, so big files take a while.

**Tools:** tick "Search the web", "Read a web page", "Calculator" or the tools of an MCP server
on an agent's card. Before it speaks it may look things up, and everyone sees what it looked
up. Add MCP servers under "Tools": a command Moot starts (e.g. `npx -y
@modelcontextprotocol/server-filesystem D:\Documents`) or a URL.

**Local models (LM Studio, Ollama):** Moot fits every prompt to the model's context window.
LM Studio tells Moot the context each loaded model got; with other servers Moot learns it when
a request is refused as too long, and tries again shorter. When the context is small, the
oldest parts of the discussion are left out first and requests take turns. LM Studio loads
models with only 4,096 tokens by default, which Moot points out at the start: for better
discussions, load the model with a context length of 16,384 or more (in LM Studio's load
settings, or `lms load <model> --context-length 16384`).

**Saved discussions and export:** discussions are saved as they run and survive a restart.
The team builder lists them: open one, take its setup to run it again, or delete it. Export a
discussion as a readable report (Markdown) or as everything (JSON).

## Commands

```
moot                         start Moot and open it in the browser
moot serve [--port 8765] [--open] [--data-dir PATH]
moot providers               the model providers Moot can use
moot models lmstudio         the models a provider offers right now
moot ask openrouter MODEL "question"
moot debate                  a short debate in the terminal
moot --version
```

The full web API is documented at http://127.0.0.1:8765/docs while Moot runs.

## Privacy and safety

- The server answers only this computer and refuses requests from other websites, since it
  has no login and uses your keys. Serving on the network is possible, with a warning.
- "Read a web page" never opens addresses on this computer or your local network.
- The calculator only does arithmetic; no code is run.
- MCP servers you add as commands run on your computer, like any program you start.
- Agents follow a global rule never to invent facts or sources, and cite their files.

## Development

Everything installs into the project's own `.venv`.

```powershell
uv sync                  # create .venv with the dev tools
uv run pytest            # Python tests
uv run ruff check .      # lint
uv run moot serve --open # run from source
```

The app's source is in `frontend/` (React + TypeScript + Vite); its built files are committed
in `src/moot/server/static/`, so running Moot needs no Node.js. See `frontend/README.md`.
`try-moot.bat` checks your model providers; `watch-debate.bat` runs a debate in the terminal.

```
src/moot/
  core/        data model: personas, rules, round plan, events
  providers/   connections to local and cloud models (chat and embeddings)
  engine/      discussion loop, moderator, alliances, polls, research step, Team Designer
  memory/      argument graph, private notebooks, agent briefings
  knowledge/   per-agent files: reading, passages, knowledge graph, search
  tools/       built-in tools and MCP servers
  storage/     saved discussions and export
  server/      web API and live event stream; serves the app
frontend/      the app's source
tests/         automated tests
scripts/       developer scripts
docs/          spec, decisions and build log
```
