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
- **Must** — Team Designer: describe the goal (e.g. "political discussion, 4 different views on immigration") → one designer agent proposes a panel with clearly different views and motivations → you review/edit each card → start
- **Should** — Balance check: second pass confirming views are distinct and fairly represented

### 3.3 Moderator modes
- **Must** — **AI moderator**: opens the discussion, runs the round plan, picks speakers where the round allows, summarises rounds, flags rule breaks, starts polls, writes the final report
- **Must** — **Human moderator (you)**: choose your display name and role (e.g. "Judge", "Journalist", "Professor"); start and advance rounds, choose who speaks, ask questions, @mention agents, start polls. No AI helper — you are in full control
- **Must** — Agents address and answer the human moderator by name and role
- **Later** — You join as a participant instead of moderator

### 3.4 Session setup: topic and rules
- **Must** — Topic + optional background text or files
- **Must** — Discussion rules applied to every agent, written freely (e.g. "answers must be logical and short", "max 80 words", "support claims with evidence")
- **Must** — Rule presets to tick: Short answers, Evidence required, No repetition, Stay on topic, Formal tone; plus custom rules
- **Must** — Moderator (AI or you) can flag a rule break; the agent must comply on its next turn
- **Should** — Hard length limit per turn

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
- **Must** — Behaviour rules: refer to others' points by name, don't repeat yourself, answer what was asked, don't drift into agreement unless genuinely persuaded, explain any change of mind
- **Must** — Click an agent to see its notebook and how its view changed
- **Should** — Argument map view (2D): explore the graph during and after the discussion
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
- **Must** — Final report (AI moderator mode): answer / recommendation, consensus points, disagreements, each agent's final stance and how it shifted, alliances, poll results, open questions
- **Should** — AI moderator asks you clarifying questions first if the topic is vague
- **Later** — Anonymous peer review mode

### 3.11 Discussion view and look
- **Must** — Futuristic visual style: dark theme, glowing accents, HUD-like panels, smooth animation
- **Proposed direction (confirm at the mockup step): "The Chamber"** — a circular arena in the centre (agents seated around a table, moderator at the head, speaker's ring glows, light beams for challenges / support / @mentions, faint lasting links for alliances); transcript on the right as glass cards; round-plan progress track and controls on the left; input bar at the bottom with @ autocomplete and /commands; polls appear over the arena. Colour meaning everywhere: teal = support, amber/red = challenge, violet = question. Geometric avatars generated from agent names
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
- **Must** — Drop files onto an agent (PDF, TXT, MD, DOCX) → indexed locally
- **Must** — Each agent retrieves only from its own knowledge and cites sources
- **Must** — Embeddings via local (Ollama) or API provider
- **Must** — Embedded vector storage, no server
- **Later** — Web pages as knowledge; knowledge shared by the whole team

### 3.14 Tools per agent
- **Must** — Built-ins: web search, fetch web page, calculator
- **Must** — MCP support: connect any MCP server and assign its tools to chosen agents

### 3.15 Sessions and output
- **Must** — Session history; reopen past discussions
- **Must** — Export transcript, argument graph, alliances and poll results; final report too in AI moderator mode
- **Should** — Token / cost counter per session for API users
- **Later** — Resume an unfinished session

### 3.16 Logging
- **Must** — Per-turn log: agent, model, tokens, time

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
- Memory library: **decided (step 5)** — own argument graph on the step-3 data model; one structured model call per turn. Graphiti (needs Neo4j; its embedded Kuzu backend is archived), Mem0 (no typed relationships) and Cognee (general document knowledge graph) did not fit. Borrowed Graphiti's idea of rules for which link types may connect which point types. Revisit Cognee / LanceDB for per-agent knowledge (RAG)
- Frontend framework (must work well with three.js for the later 3D views)
- Provider layer: **decided (step 2)** — own thin adapter on httpx; one OpenAI-compatible provider covers OpenRouter, OpenAI, Groq, DeepSeek, Gemini, Ollama, LM Studio and custom servers. Anthropic would need its own native adapter if added later
- Embedded vector store library
- Document parser (check licences)
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
