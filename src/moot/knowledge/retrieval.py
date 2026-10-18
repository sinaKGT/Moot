"""Finding what an agent knows that bears on the discussion right now.

Passages are found by their words (keyword index) and, with an embedding model, by meaning;
the two rankings are fused. The graph then adds facts: about entities named in the
discussion or close to it in meaning, about entities in the passages found, and one step
further along the links. The agent gets a short private briefing with a source for each line.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from moot.knowledge.extraction import entity_key
from moot.knowledge.store import ChunkRow, FactRow, KnowledgeStore, keyword_terms

RRF_K = 60
PASSAGES = 3
FACTS = 8
PASSAGE_CHARS = 700
MIN_ENTITY_SIMILARITY = 0.45
MIN_FACT_SIMILARITY = 0.35
MIN_PASSAGE_SIMILARITY = 0.3
MIN_FACT_SCORE = 0.6


def source_label(filename: str, page: int | None) -> str:
    return f"{filename} p.{page}" if page else filename


@dataclass
class FactHit:
    statement: str
    source: str
    score: float


@dataclass
class PassageHit:
    text: str
    source: str
    score: float


@dataclass
class KnowledgeBrief:
    facts: list[FactHit] = field(default_factory=list)
    passages: list[PassageHit] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.facts and not self.passages

    @property
    def sources(self) -> list[str]:
        found: list[str] = []
        for hit in [*self.facts, *self.passages]:
            if hit.source not in found:
                found.append(hit.source)
        return found

    def text(self) -> str:
        """The briefing as it goes into the agent's prompt."""
        if self.empty:
            return ""
        lines = ["Your own knowledge (from your files; only you can see this):"]
        if self.facts:
            lines.append("Facts:")
            lines += [f"- {hit.statement} [{hit.source}]" for hit in self.facts]
        if self.passages:
            lines.append("Passages:")
            lines += [f'- [{hit.source}] "{hit.text}"' for hit in self.passages]
        example = self.sources[0]
        lines.append(
            "Use it where it helps your point, and name the source in brackets when you do, "
            f"e.g. [{example}]. Never cite a source that isn't listed here, and don't claim "
            "more than it says."
        )
        return "\n".join(lines)


def _fuse(*rankings: list[int]) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


def _excerpt(text: str, terms: list[str], limit: int = PASSAGE_CHARS) -> str:
    """The part of a passage around its first matching word, cut at word boundaries."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    lower = flat.casefold()
    hits = [lower.find(t) for t in terms if lower.find(t) >= 0]
    start = max(0, min(hits) - limit // 4) if hits else 0
    start = flat.rfind(" ", 0, start) + 1 if start else 0
    end = start + limit
    cut = flat.rfind(" ", start, end)
    piece = flat[start : cut if cut > start else end]
    return ("…" if start else "") + piece + "…"


def search(
    store: KnowledgeStore,
    base_id: str,
    query: str,
    query_vector: list[float] | None = None,
    *,
    passages: int = PASSAGES,
    facts: int = FACTS,
) -> KnowledgeBrief:
    terms = keyword_terms(query)
    keyword = store.keyword_chunks(base_id, query, limit=20)
    meaning = (
        [
            cid
            for cid, similarity in store.similar(base_id, "chunks", query_vector, 20)
            if similarity >= MIN_PASSAGE_SIMILARITY
        ]
        if query_vector
        else []
    )
    chunk_scores = _fuse(keyword, meaning)
    top_chunks = sorted(chunk_scores, key=lambda c: -chunk_scores[c])[: max(passages, 6)]

    # entities: named in the query, close in meaning, or mentioned in the best passages
    seeds: dict[int, float] = {}
    lowered = f" {entity_key(query)} "
    for key, entity_id in store.entity_keys(base_id).items():
        if len(key) >= 3 and f" {key} " in lowered:
            seeds[entity_id] = seeds.get(entity_id, 0) + 2.0
    if query_vector:
        for entity_id, similarity in store.similar(base_id, "entities", query_vector, 8):
            if similarity >= MIN_ENTITY_SIMILARITY:
                seeds[entity_id] = seeds.get(entity_id, 0) + similarity * 2
    for rank, (_chunk_id, entity_ids) in enumerate(
        store.entities_in_chunks(top_chunks[:3]).items()
    ):
        for entity_id in entity_ids:
            seeds[entity_id] = seeds.get(entity_id, 0) + 0.5 / (rank + 1)

    # candidate facts: about those entities, one step further along the links, or close in
    # meaning to the query; each is then ranked by how well it answers the query
    rows: dict[int, FactRow] = {}
    nearness: dict[int, float] = {}
    neighbours: dict[int, float] = {}
    for fact in store.facts_about(seeds):
        rows[fact.id] = fact
        ends = sorted(
            (seeds.get(fact.subject_id, 0.0), seeds.get(fact.object_id or -1, 0.0)), reverse=True
        )
        nearness[fact.id] = ends[0] + 0.25 * ends[1]
        for other in (fact.subject_id, fact.object_id):
            if other is not None and other not in seeds:
                neighbours[other] = max(neighbours.get(other, 0.0), ends[0] * 0.4)
    for fact in store.facts_about(sorted(neighbours, key=lambda e: -neighbours[e])[:6]):
        if fact.id not in rows:
            rows[fact.id] = fact
            nearness[fact.id] = max(
                neighbours.get(fact.subject_id, 0.0), neighbours.get(fact.object_id or -1, 0.0)
            )
    meaning_of: dict[int, float] = {}
    if query_vector:
        close = [
            fid
            for fid, similarity in store.similar(base_id, "facts", query_vector, 12)
            if similarity >= MIN_FACT_SIMILARITY
        ]
        for fact in store.facts([fid for fid in close if fid not in rows]):
            rows[fact.id] = fact
            nearness[fact.id] = 0.0
        meaning_of = store.similarity_of(base_id, "facts", query_vector, rows)

    fact_scores: dict[int, float] = {}
    for fact_id, fact in rows.items():
        words = fact.statement.casefold()
        score = 0.5 * nearness[fact_id] + 0.6 * sum(1 for t in terms if t in words)
        score += 3.0 * max(meaning_of.get(fact_id, 0.0), 0.0)
        if fact.chunk_id in chunk_scores:
            score += 0.3
        if score >= MIN_FACT_SCORE:
            fact_scores[fact_id] = score

    best_facts = sorted(fact_scores, key=lambda f: -fact_scores[f])[:facts]
    chosen_passages = top_chunks[:passages]
    loaded: dict[int, ChunkRow] = store.chunks(
        {*chosen_passages, *(rows[f].chunk_id for f in best_facts)}
    )
    brief = KnowledgeBrief()
    seen_statements: set[str] = set()
    for fact_id in best_facts:
        fact, chunk = rows[fact_id], loaded.get(rows[fact_id].chunk_id)
        key = fact.statement.casefold()
        if chunk is None or key in seen_statements:
            continue
        seen_statements.add(key)
        brief.facts.append(
            FactHit(fact.statement, source_label(chunk.filename, chunk.page), fact_scores[fact_id])
        )
    for chunk_id in chosen_passages:
        chunk = loaded.get(chunk_id)
        if chunk is not None:
            brief.passages.append(
                PassageHit(
                    _excerpt(chunk.text, terms),
                    source_label(chunk.filename, chunk.page),
                    chunk_scores[chunk_id],
                )
            )
    return brief
