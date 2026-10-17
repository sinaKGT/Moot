"""Briefings: what an agent is reminded of before it speaks.

Built from its private notebook and the shared argument graph: its own position, the
challenges and questions aimed at it that it hasn't dealt with, the strongest points
it hasn't answered yet, alliance invitations waiting for an answer, who is allied
with whom, and recent poll results.
"""

from __future__ import annotations

from moot.core import EdgeType, Node, NodeType, Persona, SessionState
from moot.memory.alignment import alliance_lines, invitation_lines
from moot.memory.votes import poll_lines

MAX_ATTENTION_ITEMS = 3


def _visible(node: Node, agent: Persona, hidden_round: int | None) -> bool:
    """In a blind round, points other agents made in that round are not visible."""
    return not (
        hidden_round is not None
        and node.round_index == hidden_round
        and node.speaker_id != agent.id
    )


def notebook_lines(state: SessionState, agent: Persona) -> list[str]:
    notebook = state.notebooks.get(agent.id)
    if notebook is None:
        return []
    names = {a.id: a.name for a in state.config.agents}
    lines = []
    if notebook.verdict or notebook.stance:
        verdict = notebook.verdict.upper() if notebook.verdict else "UNDECIDED"
        stance = f": {notebook.stance}" if notebook.stance else ""
        lines.append(
            f"- Your answer so far: {verdict} ({round(notebook.confidence * 100)}% sure){stance}"
        )
    if notebook.key_arguments:
        lines.append(f"- Your key arguments: {'; '.join(notebook.key_arguments)}")
    if notebook.concessions:
        lines.append(f"- Points you have accepted: {'; '.join(notebook.concessions)}")
    if notebook.judgements:
        judged = "; ".join(
            f"{names.get(agent_id, agent_id)}: {text}"
            for agent_id, text in notebook.judgements.items()
        )
        lines.append(f"- How you rate the others: {judged}")
    if notebook.allies:
        lines.append(f"- Your allies: {', '.join(names.get(a, a) for a in notebook.allies)}")
    if notebook.opponents:
        lines.append(f"- Your opponents: {', '.join(names.get(a, a) for a in notebook.opponents)}")
    if notebook.strategy:
        lines.append(f"- Your plan: {notebook.strategy}")
    if notebook.questions_to_ask:
        lines.append(f"- Questions you wanted to ask: {'; '.join(notebook.questions_to_ask)}")
    return lines


def attention_lines(
    state: SessionState, agent: Persona, hidden_round: int | None = None
) -> list[str]:
    graph = state.graph

    def who(node: Node) -> str:
        return state.config.speaker_label(node.speaker_id)

    def answered_by_me(node: Node) -> bool:
        return any(
            graph.node(e.source).speaker_id == agent.id
            for e in graph.incoming(node.id)
            if e.type in (EdgeType.CHALLENGES, EdgeType.ANSWERS, EdgeType.CONCEDES_TO)
        )

    lines: list[str] = []

    challenges = []
    for edge in reversed(graph.edges):
        if edge.type is not EdgeType.CHALLENGES:
            continue
        source, target = graph.node(edge.source), graph.node(edge.target)
        if (
            target.speaker_id == agent.id
            and source.speaker_id != agent.id
            and _visible(source, agent, hidden_round)
            and not answered_by_me(source)
            and source.id not in {c.id for c, _ in challenges}
        ):
            challenges.append((source, target))
    for source, target in challenges[:MAX_ATTENTION_ITEMS]:
        lines.append(f'- {who(source)} challenged your point "{target.text}": {source.text}')

    questions = [
        node
        for node in graph.find(node_type=NodeType.QUESTION)
        if agent.id in node.addressed_to
        and _visible(node, agent, hidden_round)
        and not answered_by_me(node)
    ]
    for question in questions[-MAX_ATTENTION_ITEMS:]:
        lines.append(f"- {who(question)} asked you: {question.text}")

    already = {source.id for source, _ in challenges} | {q.id for q in questions}
    opposing = [
        node
        for node in graph.find(node_type=NodeType.CLAIM)
        if node.speaker_id not in (agent.id, None)
        and node.id not in already
        and _visible(node, agent, hidden_round)
        and not answered_by_me(node)
    ]
    opposing.sort(key=lambda n: len(graph.incoming(n.id, EdgeType.SUPPORTS)), reverse=True)
    for node in opposing[:MAX_ATTENTION_ITEMS]:
        lines.append(f"- Point you haven't answered, from {who(node)}: {node.text}")
    lines += invitation_lines(state, agent)
    return lines


def build_briefing(state: SessionState, agent: Persona, hidden_round: int | None = None) -> str:
    """The briefing text, or "" when there is nothing to say yet."""
    sections = []
    notes = notebook_lines(state, agent)
    if notes:
        sections.append("Your private notes (only you can see these):\n" + "\n".join(notes))
    attention = attention_lines(state, agent, hidden_round)
    if attention:
        sections.append("What needs your attention:\n" + "\n".join(attention))
    alliances = alliance_lines(state, agent)
    if alliances:
        sections.append("Alliances (everyone can see these):\n" + "\n".join(alliances))
    polls = poll_lines(state, agent)
    if polls:
        sections.append("Poll results (everyone can see these):\n" + "\n".join(polls))
    return "\n\n".join(sections)


def earlier_points(
    state: SessionState, message_ids: set[str], agent: Persona, hidden_round: int | None = None
) -> list[str]:
    """Claims from older messages, as recorded in the graph, for agents' short memory of
    what was said before the recent messages."""
    return [
        f"{state.config.speaker_label(node.speaker_id)}: {node.text}"
        for node in state.graph.nodes.values()
        if node.message_id in message_ids
        and node.type in (NodeType.CLAIM, NodeType.EVIDENCE, NodeType.CONCESSION)
        and _visible(node, agent, hidden_round)
    ]


def pending_items(state: SessionState, agent: Persona, hidden_round: int | None = None) -> int:
    """How many challenges and questions aimed at this agent it hasn't dealt with."""
    return sum(
        1
        for line in attention_lines(state, agent, hidden_round)
        if not line.startswith("- Point you haven't answered")
    )
