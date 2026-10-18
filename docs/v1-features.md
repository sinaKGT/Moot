# Moot — v1 Feature Spec

**Status:** agreed 17 Oct 2026, updated 17 Oct 2026 · Personal project
**Package name:** `moot` (free on PyPI as of 17 Oct 2026)

## 1. Concept

Moot is a lightweight, local-first platform where a team of AI agents discusses a topic. You build the team visually and give each agent its own persona, motivation, model, knowledge and tools. You define how the discussion runs, round by round, and set the rules. Then either an AI moderator runs it, or you moderate it yourself under your own name and role.

Agents behave like real people: each has its own goals, they remember the discussion through a shared **argument graph** and a **private notebook**, they can form and break **alliances**, and they can be asked to **vote** at any time. Everything is monitored and visible.

The interface has a futuristic look, with 3D views (three.js) as the end goal. Works with local models or your own API keys.

## 2. Key decisions

| #   | Decision | Reason |
|-----|----------|--------|
| D1  | Python backend that also serves the web UI (one process) | Best RAG / MCP / LLM libraries; LLM latency dominates, not backend speed |
| D2  | Install: `uv tool install moot` → run `moot` → opens in browser | Isolated environment, nothing installed globally, no Docker required |
| D3  | Docker image is optional, never required | Docker Desktop + WSL2 is heavier than the app itself |
| D4  | Own discussion engine + small focused libraries; no agent framework (no Agno / CrewAI / AutoGen) | The discussion loop is the core and must be fully ours |
| D5  | Engine separated from UI via an event stream | 2D UI, 3D views, API or a desktop wrapper all plug into the same engine |
| D6  | Personas built from structured fields; engine assembles the prompt | Easy to define, consistent quality |
| D7  | "No invented facts or sources" is a global rule for every agent, in every situation | Agents may persuade and use tactics, but never fabricate |
| D8  | Embedded storage only (files + embedded DB); no database server | Lightweight install |
| D9  | The engine runs a **Round Plan** (data), not a hard-coded flow | You define the rounds; the same engine runs any discussion format |
| D10 | The moderator is a seat filled by **AI or by you** | One engine, two control modes |
| D11 | Memory = one **shared argument graph** (public record) + one **private notebook** per agent (own stance, judgements, strategy) | Like people: everyone heard the same conversation, each interprets it differently. Graph built once per turn |
| D12 | Memory lasts for one session only | Each discussion starts fresh |
| D13 | Futuristic 2D UI in v1; whether to add 3D views (three.js) is decided after v1 | Any 3D view would plug into the same event stream, so no backend changes; v1 stays simple |
| D14 | **Everything said is public**: direct conversations with one agent, alliance declarations and votes are all part of the discussion and its memory | Simple, transparent, fully monitorable |
| D15 | **No global collaborative/competitive switch**: each agent has its own motivation in its persona | More realistic: in real discussions people want different things |
| D16 | For memory, evaluate existing GitHub libraries first; build our own only if none fit | Don't rebuild what exists |

## 3. v1 features

Legend: **Must** = in v1 · **Should** = v1 if it stays simple · **Later** = v2+

### 3.1 Team builder (main screen)
- **Must** — Agent library on the side; drag an agent onto the panel to add it to the discussion
- **Must** — Click an agent card to configure it (persona, model, knowledge, tools)
- **Must** — Moderator seat, always present: AI moderator or You
- **Should** — Save/load teams as presets; export/import a team as a file to share

### 3.2 Personas
- **Must** — Structured persona fields:
  - Identity: name, avatar, colour
  - Viewpoint: what they believe / argue for
  - Motivation: what they want from the discussion (win the argument, find the truth, reach consensus, defend an interest, persuade a specific agent…)
  - Expertise: background knowledge
  - Thinking style: skeptical, optimistic, contrarian, pragmatic…
  - Behaviour: persuadable ↔ holds position; concise ↔ detailed; open to alliances ↔ lone wolf
  - Model + temperature
  - Advanced: raw prompt override
- **Must** — Starter library (Skeptic, Pragmatist, Domain Expert, Devil's Advocate, Innovator)
- **Must** — Global rules for all agents: no fabricated facts or sources, flag uncertainty, represent other views accurately
- **Must** — Team Designer: describe the goal (e.g. "political discussion, 4 different views on immigration") → one designer agent proposes a panel with clearly different views and motivations → you review/edit each card → start — **built (step 18)**
- **Should** — Balance check: second pass confirming views are distinct and fairly represented — **built (step 18)**

### 3.3 Moderator modes
- **Must** — **AI moderator**: opens the discussion, runs the round plan, picks speakers where the round allows, summarises rounds, flags rule breaks, starts polls, writes the final report
- **Must** — **Human moderator (you)**: choose your display name and role (e.g. "Judge", "Journalist", "Professor"); start and advance rounds, choose who speaks, ask questions, @mention agents, start polls. No AI helper — you are in full control
- **Must** — Agents address and answer the human moderator by name and role
- **Later** — You join as a participant instead of moderator

### 3.4 Session setup: topic and rules
- **Must** — Topic + optional background text or files
- **Must** — Discussion rules applied to every agent, written freely (e.g. "answers must be logical and short", "max 80 words", "support claims with evidence")
- **Must** — Rule presets to tick: Answer first (Yes / No / I don't know, then explain), One point per turn (these two on by default), Short answers, Evidence required, No repetition, Stay on topic, Formal tone; plus custom rules
- **Must** — Moderator (AI or you) can flag a rule break; the agent must comply on its next turn
- **Should** — Hard length limit per turn
- **Must** — Decision mode: list 2–8 options and the discussion becomes a decision. Agents note the option they favour (shown on the table and the panel), a vote round asks "Which option should we choose?", and the AI moderator's conclusion gives Decision / Why / Risks / Revisit if / Next steps, then the question for the audience. A "Decision" format template: Options (blind) → Criteria → Weigh the options (moderator picks, @ allowed) → Pre-mortem → Decide (vote)

### 3.5 Round plan (discussion format)
- **Must** — You define how many rounds there are and what each round is for
- **Must** — Each round has:
  - Name and instruction (e.g. "give your opinion", "challenge another agent's argument")
  - Speaking order: fixed order / moderator picks / free
  - Turns per agent
  - Blind or open: whether agents see each other's answers in this round
  - Whether @mentions are allowed
  - Optional length limit
- **Must** — Built-in round types: Opening statements, Challenge, Q&A (@mentions), Rebuttal, Free discussion, Vote, Closing statements
- **Must** — Edit the plan before starting: add, remove, reorder rounds
- **Must** — Format templates, e.g. "Classic debate" (Opening → Challenge → Rebuttal → Closing → Vote), "Brainstorm", "Quick take" (one round + summary)
- **Should** — Change the remaining plan mid-discussion: add a round, skip, end early
- **Should** — Save your own templates

### 3.6 @mentions and direct conversation
- **Must** — Typing @ opens a list of the agents to pick from (autocomplete)
- **Must** — A mention becomes a link: click it to open that agent's card and its arguments in the graph
- **Must** — Agents can @ each other with questions in rounds that allow it
- **Must** — The mentioned agent replies immediately (jumps the queue), one reply per question
- **Must** — Loop guard: limit follow-ups so two agents can't ping-pong forever
- **Must** — You can @ any agent at any time to talk to it directly; the conversation is public and part of the discussion memory
- **Must** — Mentions highlighted in the chat, with each reply linked to its question

### 3.7 Discussion memory (human-like, session only)

**Shared argument graph (public record)**
- **Must** — After each turn, a graph builder step turns the message into nodes and links:
  - Nodes: topic, sub-questions, claims, evidence/sources, questions, concessions (votes are stored with their polls and included in briefings)
  - Links: supports, challenges, answers, refines, concedes to, directed at
  - Every node records who said it and in which round
- **Must** — Tracks status: unanswered questions, challenges not yet rebutted, points conceded, open sub-questions
- **Must** — The graph builder uses a model you choose (can differ from the agents' models)

**Private notebook (per agent)**
- **Must** — Updated after each of its turns: current stance and confidence, its key arguments, what it has conceded, how it judges other agents' arguments, current allies and opponents, its strategy, questions it wants to ask
- **Must** — Hidden from other agents; visible to you

**How agents use memory**
- **Must** — Before speaking, each agent gets a briefing built from the graph and its notebook:
  - The topic and sub-questions
  - Its own claims and whether they have been challenged
  - Challenges and questions aimed at it that it hasn't answered yet
  - The strongest opposing arguments
  - Current alliances and open polls
  - What has changed since its last turn
- **Must** — The prompt is built from: persona + session rules + briefing + recent turns (no full transcript, so long discussions stay within model limits)
- **Must** — Behaviour rules: argue your own view (when disagreeing, give a reason, example or counter-argument instead of only asking for evidence), refer to others' points by name, don't repeat yourself, answer what was asked, don't drift into agreement unless genuinely persuaded, explain any change of mind
- **Must** — Click an agent to see its notebook and how its view changed
- **Should** — Argument map view (2D): explore the graph during and after the discussion — **built (step 16)**
- **Should** — Warning when two agents' stances have become near-identical
- **Later** — 3D argument graph (three.js)

### 3.8 Alliances and dynamics
- **Must** — **Declared alliances**: an agent can publicly propose an alliance to another agent (or group); the other can accept or refuse; any member can leave or break it. Each is recorded as an event
- **Must** — **Detected alignment**: the system computes how strongly each pair of agents supports or opposes each other, from the support/challenge links in the graph
- **Must** — Agents can act on alliances: back up an ally's argument, coordinate against a shared opponent, call out a broken alliance
- **Must** — **Alliance monitor**: network view of the agents (support links in one colour, opposition in another, thickness = strength), a round slider to see how it changed, and a timeline of alliance events
- **Should** — Highlight declared alliances that don't match actual behaviour (e.g. allies who keep challenging each other)
- **Later** — 3D alliance network (three.js)

### 3.9 Polls and voting
- **Must** — The moderator (AI or you) can start a poll at any time: a question plus options, or "who made the strongest case?"
- **Must** — A Vote round can also be placed in the round plan
- **Must** — Agents vote by choosing one option only (no text, no reasons); votes are public and become part of the memory
- **Must** — On "who won" polls, agents can't vote for themselves
- **Must** — Results shown like a real poll: animated bars with counts and percentages, each agent's avatar placed on the option it chose
- **Should** — Repeat the same poll later to see whether minds changed

### 3.10 Discussion engine
- **Must** — Executes the round plan, controlled by the AI moderator or by you
- **Must** — Steelman rule for viewpoint agents: argue as the view's strongest supporters would; AI moderator stays neutral
- **Must** — Conclusion (AI moderator mode): a short closing statement to the audience covering the answer the discussion points to (or that it stayed undecided), agreements, disagreements, each agent's final position and whether it changed, alliances and the vote; it ends with one open question for the audience ("Question for the audience: ..."), kept separately so the UI can show it
- **Should** — AI moderator asks you clarifying questions first if the topic is vague
- **Later** — Anonymous peer review mode

### 3.11 Discussion view and look
- **Must** — Futuristic visual style: dark theme, glowing accents, HUD-like panels, smooth animation
- **Decided (step 13): "The Chamber"**, chosen over a denser "Control Room" HUD and a minimal "Stage" from the mockups (canvas "Moot UI mockups": discussion screen, team builder, vote and conclusion, argument map and alliances). Look: deep navy ground, glass panels, Space Grotesk with JetBrains Mono for labels; agents get their own colours (blue, coral, lime, …) kept apart from the meaning colours. Layout — a circular arena in the centre (agents seated around a table, moderator at the head, speaker's ring glows, light beams for challenges / support / @mentions, faint lasting links for alliances); transcript on the right as glass cards; round-plan progress track and controls on the left; input bar at the bottom with @ autocomplete and /commands; polls appear over the arena. Colour meaning everywhere: teal = support, amber/red = challenge, violet = question. Geometric avatars generated from agent names
- **Must** — Group-chat stream with avatar + colour per agent, streaming text
- **Must** — Live round-table visual: who is speaking, who is next, current alliances
- **Must** — Round progress from the round plan (e.g. "Round 2 of 4 — Challenge")
- **Must** — Interject anytime, @ an agent, start a poll, pause / resume / stop
- **Must** — Show when an agent used a tool or its knowledge, with the source
- **Should** — "Continue?" checkpoint after N turns
- **Should** — Moderator decision trace (why this speaker, why stop)
- **Later** — 3D round table (three.js): agents seated around a table, speaker highlighted, alliances visible

### 3.12 Models
- **Must** — Bring your own key: OpenAI, Anthropic, Google Gemini, OpenRouter, Groq, DeepSeek
- **Must** — Local: Ollama + any OpenAI-compatible server (LM Studio, llama.cpp, vLLM)
- **Must** — Mix freely per agent (e.g. cloud moderator, local participants)
- **Must** — API keys stored only on the user's machine
- **Should** — "Test connection" per provider; startup health check for local servers
- **Should** — Indicate which models support tool calling

### 3.13 Knowledge (RAG) per agent
- **Must** — Drop files onto an agent (PDF, TXT, MD, DOCX) → indexed locally — **built (step 19)**
- **Must** — Each agent retrieves only from its own knowledge and cites sources — **built (step 19)**
- **Must** — Embeddings via local (Ollama) or API provider — **built (step 19)**: any OpenAI-compatible provider (LM Studio, Ollama, OpenRouter, OpenAI…)
- **Must** — Embedded vector storage, no server — **built (step 19)**: SQLite + numpy, with the files' knowledge graph
- **Later** — Web pages as knowledge; knowledge shared by the whole team

### 3.14 Tools per agent
- **Must** — Built-ins: web search, fetch web page, calculator
- **Must** — MCP support: connect any MCP server and assign its tools to chosen agents

### 3.15 Sessions and output
- **Must** — Session history; reopen past discussions — **built (step 17)**
- **Must** — Export transcript, argument graph, alliances and poll results; final report too in AI moderator mode — **built (step 17)**
- **Should** — Token / cost counter per session for API users
- **Later** — Resume an unfinished session

### 3.16 Logging
- **Must** — Per-turn log: agent, model, tokens, time — **built (step 17)**: every message keeps its model and how long it took; shown on the card and in the export's turn log

### 3.17 Install and footprint
- **Must** — One-command install, no Docker, no database server
- **Must** — Runs fully offline with local models

## 4. Later (v2+) parking lot
- 3D round table, 3D argument graph and 3D alliance network (three.js)
- You as a participant
- Desktop installer (wrap the web UI)
- Parallel generation when hardware allows
- Cloud-hosted / multi-user mode

## 5. Decisions log and open items

**Decided:**
- Memory lasts one session only: shared argument graph + private notebooks
- No AI helper when Sina moderates
- @ opens a list of agents; mentions are links; the mentioned agent replies immediately
- Everything said is public, including direct conversations with one agent
- Models are assumed capable; no special handling for weak graph extraction
- No global collaborative/competitive mode; motivation is per agent
- "No invented facts or sources" applies always
- Alliances: both declared by agents and detected by the system
- Winner and other questions decided by agent polls, started by the moderator at any time or placed in the round plan; agents just pick an option, no text

**Build time:**
- Knowledge per agent: **built (step 19)** — Sina chose "graph memory": each agent's files become its own knowledge graph, like the argument graph. Drop PDF, Word, text or Markdown files on an agent's card (up to 25 MB each); in the background each file is read (PDF pages kept for citing), split into overlapping passages of about 1,200 characters at paragraph and sentence boundaries, embedded with the chosen embedding model (LM Studio by default when it has one; "None" searches by words), then mapped into the graph: one structured call per passage (the discussion's model) lists the entities it is about and the facts linking them, each fact kept with its passage; entities with the same name merge across passages and files; entities and facts are embedded too. The card shows each file's progress and notes, the graph's size, and "Try" shows exactly what the agent would be told. Before each of its turns (and answers to @mentions), the agent's own files are searched: passages by words (SQLite FTS5) and meaning, fused; then facts about the entities named in or close to the discussion, about the entities in the best passages, one step further along the links, or close in meaning, each ranked by how well it answers the moment. The agent gets a short private briefing (up to 8 facts and 3 passages, each with its source, e.g. [report.pdf p.2]) and is told to cite and never cite anything not listed. The transcript card shows which files a turn drew on ("From its files: …"), and so does the export. Failures never lose a file or stop a discussion: a file stays searchable by its words with a note; mapping stops after 5 failed calls in a row; at most 400 passages per file are mapped; files cut off by a restart are marked. Stored in `knowledge.db` next to the saved discussions; removing a file removes everything learned from it. Knowledge stays with the agent's setup, so "Use this setup" keeps it; a discussion whose agent's files were deleted is refused with a clear message. Tried live with OpenRouter's free Nemotron model: the mapping prompt gives clean, self-contained facts, but takes about 45 seconds per passage on that free model, so big files map slowly (a local model in LM Studio, or a faster paid one, is quicker)
- Team Designer: **built (step 18)** — "Team Designer" in the team builder: describe the goal in your words (examples offered), optionally the number of agents, and it uses the discussion's model. One structured call proposes the topic as a clear question, the format (classic debate, brainstorm, quick take or decision), the options for a decision (the ones you named, else realistic ones) and the panel: made-up first names (never real people), role, viewpoint written to the agent, motivation, expertise, thinking style and the three traits, varied so not everyone is stubborn or agreeable, with every view at its strongest and no invented facts. The balance check (on by default) is a second call that reviews the panel for views that are the same in other words or a main position nobody holds, and may revise it; if it fails, the first panel stays with a note. The server tidies whatever comes back (unique names that can't clash with the moderator or @all, traits in range, a known format, a decision only with two or more options, at most 8 agents or the number asked). The proposal is shown first (topic, options, each agent's view, why this panel, what the balance check found); "Use this panel" puts topic, panel, format and options into the builder (the model, moderator, rules and background stay), with a note to review each card and Undo. Tried live with OpenRouter's free Nemotron model: about 25 seconds for both calls
- Saving and export: **built (step 17)** — every discussion is saved as it runs in one SQLite file (`moot.db`, no database server) in the app data folder (`%LOCALAPPDATA%\Moot` on Windows; `MOOT_DATA_DIR` or `moot serve --data-dir` to choose another). It keeps each session's latest state and its events in order (token deltas aren't kept: the finished message has the whole text), so reopening a discussion after a restart replays it exactly as it was shown, map and alliances included, and viewers resume by event number as before. A discussion still running when Moot stopped is ended there as "stopped", with a notice; one that was set up but not started can still start. The database has a schema version, and one made by a newer Moot is refused rather than damaged. The team builder lists the saved discussions (status, date, panel) to open, to take the setup of ("Use this setup", to run it again or change it) or to delete (after a confirmation; gone from disk). Export from the discussion screen: a Markdown report (conclusion and audience question, final positions, polls with who voted for what, alliances with their timelines, who backed whom, the argument map in words, the transcript round by round, the turn log with model, tokens and seconds, and the setup) or everything as JSON (the full state and the saved events). Writing to the database never stops a discussion: a failed save is logged
- Argument map and alliance monitor: **built (step 16)**, following mockup A4 — a second view of the discussion ("Map & alliances" next to "Discussion"), live while it runs. The argument map has one column per speaker and one row per round, so you read who said what over time; links between points are drawn as curves (supports teal, challenges amber, answers grey, refines dashed, concedes dotted) that leave and enter the facing sides of the cards. Links to the topic become a FOR / AGAINST badge and links to the moderator's sub-questions a Q1… badge instead of lines; questions nobody answered are dashed. Choosing a point lights up its links and shows its text, sources and links (in both directions, each a step to the linked point) with "Show in the discussion", which jumps to the message in the transcript. The map is rebuilt from the event stream alone: the session start carries the graph so far (the topic) and each graph update carries its new points and links in full. The alliance monitor (from the server's alliance view, refreshed when the graph or an alliance changes): a network of the agents (teal = backed each other, amber = opposed, grey = mixed; thickness = how much; a teal band for declared allies; dashed lines for open invitations; "!" for allies at odds), each agent's side on the topic (or, in a decision, the option it favours), the pairs in words with counts, warnings about allies who challenge each other, and each alliance's timeline. One round slider shows both as they were after any round. The layout is fixed-size and tested (no overlapping cards, lines start and end on card edges)
- Team builder and decisions: **built (step 15)** — the start screen is now the team builder, following mockup A2: the agent library (drag onto the table, or press to add; "+ New agent"), the table with the moderator seat at the head (press a seat to edit; "+ Seat"), the agent card (name, colour, role, viewpoint, motivation, expertise, thinking style, three trait sliders, optionally its own model, and an advanced whole-prompt override), the discussion's model (also runs the moderator and the argument map), moderator (AI or you, with name and role), rules (presets, your own rules, max words, alliances on/off) and the round-plan editor (format template, then per round: type, name, instruction, speaking order, turns, blind, @ allowed, max words; reorder, remove, add). It starts from the example panel and topic; the draft is remembered in the browser. Problems are listed in plain words before starting, and the server's validation errors are translated the same way ("Agent 2 · name: …"). Decision mode (see 3.4): tick "This is a decision between options", add the options and use the Decision format; during the session an Options panel shows who leans towards each option, and the conclusion is shown in its parts with the decision highlighted
- Discussion screen: **built (step 14)** — React 19 + TypeScript + Vite in `frontend/`, built into `src/moot/server/static/` and committed so running Moot needs no Node.js; served at `/` by `moot serve`. A pure reducer turns the live event stream into the screen (a reload replays it); events now carry verdicts (NotebookUpdated) and who challenged or supported whom (GraphUpdated links) for the round table. The Chamber: round plan, panel with verdicts and controls on the left; the round table with the speaker's glow, beams that always arc around the table (challenge amber, support teal, question violet, alliance dashed) and the poll card over it; the transcript with streaming cards, tags, @mention colours and the conclusion with its audience question; the input bar with @ and /command autocomplete; "your move" prompts when you moderate. Fonts bundled (offline). A quick-start screen stands in until the team builder (step 15). Agent colours moved away from the meaning colours
- Backend API: **decided (step 12)** — FastAPI + uvicorn, started with `moot serve` (default 127.0.0.1:8765). REST for setup and control: providers and their models (whether a key is set, never the key), library (starter personas, templates, rule presets), sessions (create from a full config or the demo, list, view, delete), controls (start, pause, resume, stop, finish, next-round, end-round, messages, floor, reminders, polls). Live events as server-sent events with the sequence number as id, so reconnecting viewers resume (Last-Event-ID / ?after=), plus a heartbeat. Views of alliances (any round) and the argument map. Sessions are in memory until persistence (step 17). All routes are async so they run on the engine's event loop. Local only: requests must be addressed to this machine (DNS rebinding) and cross-site requests from other web pages are refused; serving on the network is possible with a warning. A plain test page at /dev until the real UI
- Human moderator: **decided (step 11)** — the engine waits for the human before each round, for every speaker in rounds where the moderator chooses (turns per agent is only a guide there; the round runs until they move on), and at the end of the plan, announcing each wait with a WaitingForModerator event. Controls: give_floor (a name, or none for a fair pick; in automatic rounds it means an extra turn next), end_round (end and wait), next_round (end and start the next; in the last round it only ends it, and once the plan is done it finishes), finish (end normally, counted as completed), remind (a public moderator line the agent sees before its next turn), plus say, open_poll, pause, resume and stop. No AI help: no summaries, speaker choices, report, topic check or automatic rule reminders (rule breaks are still flagged to the human). Agents are told the moderator is a person and to address them by name. "@all" / "@everyone" asks every agent at once. Moderator and user messages stay visible in blind rounds; only other agents' answers are hidden
- Polls: **decided (step 10)** — each agent votes with one short structured call (JSON mode), seeing the discussion and the allowed options as letters, never others' votes in the same poll; all agents vote at the same time. A wrong option gets one more try; an agent that still can't vote is left out with a notice (voting failures never end the discussion). With only one allowed option the vote is cast without a call. The user (or the human moderator) can open a poll at any time, also while paused; a vote round in the plan runs its poll; the AI moderator may open one poll of its own after a round summary (hidden `<poll>` line, at most one per session by default, never right before a vote round). Repeating a poll links it to the earlier one so results can be compared. Results go into every briefing (with who voted for what) and into the moderator's summaries and report
- Alliances: **decided (step 9)** — agents declare actions in a hidden `<alliance>{...}</alliance>` line (propose with a short shared goal, accept, refuse, leave with a reason); the engine applies them, reports each as an event and everyone sees them in their briefing. An agent is in at most one alliance: proposing while allied invites into it, proposing back to an inviter accepts, accepting another invitation leaves the old alliance. Not possible in blind rounds; can be switched off per session. Invitations count as items to answer, so the moderator tends to give the invitee the floor. Every action is kept in the alliance's history (timeline, round slider). Detected alignment: support / refine / concede links count for, challenge links against, per pair and up to any round; sides on the topic from links to the topic; allies who challenge each other more than they support each other since allying are flagged and shown to everyone. An alliance never overrides honesty
- @mentions: **decided (step 8, revised after the first live runs)** — a mentioned agent answers right after the message, before the round goes on. If it hasn't had its turn in the round yet, the answer is its turn: it answers first, then makes its own point, and doesn't speak again in a separate turn (otherwise agents spoke twice and repeated themselves). If it has already spoken, it answers briefly without repeating itself. Never a turn in vote rounds. Loop guard: a question gets one answer, that answer can get one answer back (chain of 2), and agent-to-agent replies per round are capped at the round's number of normal turns; later questions wait for the agent's normal turn. Mentions only trigger replies in rounds that allow them and never in blind rounds. Agents the user @mentions always answer, at any time, also while the session is paused. Replies skip the answer-first check, which is about the topic question
- Memory library: **decided (step 5)** — own argument graph on the step-3 data model; one structured model call per turn. Graphiti (needs Neo4j; its embedded Kuzu backend is archived), Mem0 (no typed relationships) and Cognee (general document knowledge graph) did not fit. Borrowed Graphiti's idea of rules for which link types may connect which point types. Revisit Cognee / LanceDB for per-agent knowledge (RAG)
- Frontend framework (must work well with three.js for the later 3D views)
- Provider layer: **decided (step 2)** — own thin adapter on httpx; one OpenAI-compatible provider covers OpenRouter, OpenAI, Groq, DeepSeek, Gemini, Ollama, LM Studio and custom servers. Anthropic would need its own native adapter if added later
- Embedded vector store library: **decided (step 19)** — SQLite (as for saved discussions) with numpy for the vector maths, chosen over sqlite-vec (pre-1.0 binary extension) and LanceDB (~100 MB with pyarrow); SQLite's own vector extension (vec1, merged into SQLite's source in September 2026) can replace numpy once Python ships it
- Document parser: **decided (step 19)** — pypdf (BSD-3) for PDF and python-docx (MIT, with lxml, BSD-3) for Word; PyMuPDF was avoided because it is AGPL
- GPU / VRAM on the dev machine → default local models

## 6. Proposed build order
One step at a time; each step is checked and validated before the next.

1. Project skeleton: isolated uv environment, local git, repo structure
2. Provider layer: one local (Ollama) + one cloud provider, streaming
3. Core data model: persona, session rules, round plan, argument graph, alliances, polls, events
4. Engine core: runs a simple round plan (Opening → Challenge) with 2 agents, tested from a script
5. Memory: evaluate GitHub libraries, then build the argument graph builder
6. Private notebooks + agent briefing and prompt assembly
7. AI moderator: speaker selection, summaries, rule checks, final report
8. @mentions, direct conversation, loop guard
9. Alliances: declare / accept / break + detected alignment
10. Polls and vote rounds
11. Human-moderator mode (engine side: control commands)
12. Backend API (REST + live event stream)
13. UI mockups: agree on the futuristic look
14. UI: discussion view (with @ autocomplete and polls)
15. UI: team builder, agent configuration, round-plan editor, rules
16. UI: 2D argument map and alliance monitor
17. Session persistence + export
18. Team Designer
19. Knowledge (RAG) per agent
20. Tools: built-ins + MCP
21. Packaging (`uv tool install moot`), README

After v1: 3D round table, 3D argument graph and 3D alliance network (three.js)
