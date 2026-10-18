"""Agents' knowledge inside the server: adding files and indexing them in the background
(read, split, embed, map into the graph, embed the entities), and briefing an agent before
it speaks.

Indexing never blocks the discussion or the app; a file's progress is kept on it. A failed
embedding or mapping call doesn't lose the file: it stays searchable by its words, with a note.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from moot.core.persona import ModelRef
from moot.knowledge.chunking import chunk_sections
from moot.knowledge.extraction import entity_key, extract
from moot.knowledge.parsers import UnreadableFile, kind_of, read_sections
from moot.knowledge.retrieval import KnowledgeBrief, search
from moot.knowledge.store import BaseInfo, DocumentInfo, KnowledgeError, KnowledgeStore
from moot.providers import (
    Embedder,
    Provider,
    ProviderError,
    StructuredOutputError,
    budgeted,
    create_provider,
)

log = logging.getLogger(__name__)

ProviderFactory = Callable[[str], Provider]
MAX_GRAPH_PASSAGES = 400  # passages per file mapped into the graph
MAX_FAILURES_IN_A_ROW = 5  # mapping stops after this many failed calls in a row
QUERY_CHARS = 2000


class KnowledgeService:
    def __init__(
        self,
        store: KnowledgeStore,
        provider_factory: ProviderFactory | None = None,
        *,
        concurrency: int = 2,
    ) -> None:
        self.store = store
        self._factory = provider_factory or create_provider
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._concurrency = concurrency
        for document_id in store.interrupted():
            store.update_document(
                document_id,
                status="failed",
                error="Moot stopped while this file was being indexed. Remove it and add it again.",
            )

    # ------------------------------------------------------------------ files

    def create_base(self, name: str, embedding: ModelRef | None) -> BaseInfo:
        return self.store.create_base(name, embedding)

    def add_document(
        self, base_id: str, filename: str, data: bytes, model: ModelRef | None
    ) -> DocumentInfo:
        """Accept a file and index it in the background. Raises UnreadableFile for a type
        Moot can't read, KnowledgeError for an unknown knowledge base."""
        name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip() or "file"
        kind_of(name)
        document = self.store.add_document(base_id, name, len(data))
        self._jobs[document.id] = asyncio.create_task(
            self._index(base_id, document.id, name, data, model), name=f"moot-index-{document.id}"
        )
        return document

    async def delete_document(self, base_id: str, document_id: str) -> None:
        if self.store.document_base(document_id) != base_id:
            raise KnowledgeError(f"No file {document_id} in {base_id}")
        await self._cancel(document_id)
        self.store.delete_document(document_id)

    async def delete_base(self, base_id: str) -> bool:
        if not self.store.exists(base_id):
            return False
        for document in self.store.documents(base_id):
            await self._cancel(document.id)
        return self.store.delete_base(base_id)

    async def wait(self) -> None:
        """Until every file being indexed is done (tests)."""
        while self._jobs:
            await asyncio.gather(*list(self._jobs.values()), return_exceptions=True)

    async def shutdown(self) -> None:
        for document_id in list(self._jobs):
            await self._cancel(document_id)
        self.store.close()

    async def _cancel(self, document_id: str) -> None:
        job = self._jobs.pop(document_id, None)
        if job is not None and not job.done():
            job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await job

    # ------------------------------------------------------------------ indexing

    async def _index(
        self, base_id: str, document_id: str, filename: str, data: bytes, model: ModelRef | None
    ) -> None:
        store = self.store
        providers: dict[str, Provider] = {}

        def provider(ref: ModelRef) -> Provider:
            if ref.provider not in providers:
                providers[ref.provider] = budgeted(self._factory(ref.provider))
            return providers[ref.provider]

        try:
            store.update_document(document_id, status="indexing", stage="reading")
            sections = await asyncio.to_thread(read_sections, filename, data)
            chunks = chunk_sections(sections)
            ids = store.add_chunks(
                base_id, document_id, [(c.index, c.page, c.text) for c in chunks]
            )
            store.update_document(document_id, chunks_total=len(ids))

            embedding, _dims = store.embedding_of(base_id)
            if embedding is not None:
                store.update_document(document_id, stage="embedding")
                await self._embed_chunks(
                    base_id,
                    document_id,
                    ids,
                    [c.text for c in chunks],
                    provider(embedding),
                    embedding,
                )

            if model is not None:
                store.update_document(document_id, stage="mapping")
                await self._map(base_id, document_id, ids, chunks, filename, provider(model), model)
                if embedding is not None:
                    store.update_document(document_id, stage="linking")
                    await self._embed_entities(base_id, document_id, provider(embedding), embedding)
            else:
                store.add_note(document_id, "No model was chosen, so no knowledge graph was built.")
            store.update_document(document_id, status="ready", stage="")
        except asyncio.CancelledError:
            raise
        except UnreadableFile as exc:
            store.update_document(document_id, status="failed", stage="", error=str(exc))
        except KnowledgeError:
            pass  # the file or its knowledge base was removed meanwhile
        except Exception as exc:  # noqa: BLE001 - a file never takes the server down
            log.exception("indexing %s failed", filename)
            with contextlib.suppress(KnowledgeError):
                store.update_document(
                    document_id, status="failed", stage="", error=f"{type(exc).__name__}: {exc}"
                )
        finally:
            self._jobs.pop(document_id, None)
            for opened in providers.values():
                with contextlib.suppress(Exception):
                    await opened.aclose()

    async def _embed_chunks(
        self,
        base_id: str,
        document_id: str,
        ids: list[int],
        texts: list[str],
        provider: Provider,
        ref: ModelRef,
    ) -> None:
        vectors = await self._embed(document_id, provider, ref, texts, "passages")
        if vectors is None:
            return
        _, dims = self.store.embedding_of(base_id)
        if dims and len(vectors[0]) != dims:
            self.store.add_note(
                document_id,
                f"{ref.model} gave vectors of a different size than before; meaning search is "
                "off for this file.",
            )
            return
        if not dims:
            self.store.set_dims(base_id, len(vectors[0]))
        self.store.set_chunk_vectors(base_id, list(zip(ids, vectors, strict=True)))

    async def _embed_entities(
        self, base_id: str, document_id: str, provider: Provider, ref: ModelRef
    ) -> None:
        """Vectors for the new entities and facts, so they can be found by meaning too."""
        for what, pending, save in (
            (
                "entities",
                self.store.entities_without_vectors(base_id),
                self.store.set_entity_vectors,
            ),
            ("facts", self.store.facts_without_vectors(base_id), self.store.set_fact_vectors),
        ):
            if not pending:
                continue
            vectors = await self._embed(document_id, provider, ref, [t for _, t in pending], what)
            _, dims = self.store.embedding_of(base_id)
            if vectors is not None and (not dims or len(vectors[0]) == dims):
                save(
                    base_id, [(row_id, v) for (row_id, _), v in zip(pending, vectors, strict=True)]
                )

    async def _embed(
        self, document_id: str, provider: Provider, ref: ModelRef, texts: list[str], what: str
    ) -> list[list[float]] | None:
        if not isinstance(provider, Embedder):
            self.store.add_note(
                document_id, f"{ref.provider} can't make embeddings; search uses words only."
            )
            return None
        try:
            return await provider.embed(ref.model, texts)
        except ProviderError as exc:
            self.store.add_note(
                document_id, f"Embedding the {what} failed ({exc}); search uses words for them."
            )
            return None

    async def _map(
        self,
        base_id: str,
        document_id: str,
        ids: list[int],
        chunks: list,
        filename: str,
        provider: Provider,
        model: ModelRef,
    ) -> None:
        store = self.store
        todo = list(zip(ids, chunks, strict=True))[:MAX_GRAPH_PASSAGES]
        if len(ids) > MAX_GRAPH_PASSAGES:
            store.add_note(
                document_id,
                f"Only the first {MAX_GRAPH_PASSAGES} of {len(ids)} passages were mapped into the "
                "graph; all of them can be searched.",
            )
        gate = asyncio.Semaphore(self._concurrency)
        state = {"mapped": 0, "failed": 0, "in_a_row": 0, "stopped": None}

        async def one(chunk_id: int, chunk) -> None:
            if state["stopped"]:
                return
            async with gate:
                if state["stopped"]:
                    return
                source = f"{filename}, page {chunk.page}" if chunk.page else filename
                try:
                    found = await extract(provider, model.model, chunk.text, source)
                except (ProviderError, StructuredOutputError) as exc:
                    state["failed"] += 1
                    state["in_a_row"] += 1
                    if state["in_a_row"] >= MAX_FAILURES_IN_A_ROW:
                        state["stopped"] = str(exc)
                    store.update_document(document_id, chunks_failed=state["failed"])
                    return
            state["in_a_row"] = 0
            keys: dict[str, int] = {}
            for entity in found.entities:
                key = entity_key(entity.name)
                keys[key] = store.upsert_entity(
                    base_id, key, entity.name, entity.type, entity.description
                )
            store.add_mentions(chunk_id, keys.values())
            for fact in found.facts:
                subject = keys.get(entity_key(fact.subject))
                if subject is None:
                    continue
                obj = keys.get(entity_key(fact.object))
                store.add_fact(
                    base_id,
                    chunk_id,
                    subject,
                    obj,
                    "" if obj else fact.object,
                    fact.relation,
                    fact.statement,
                )
            state["mapped"] += 1
            store.update_document(document_id, chunks_mapped=state["mapped"])

        await asyncio.gather(*(one(chunk_id, chunk) for chunk_id, chunk in todo))
        if state["stopped"]:
            store.add_note(
                document_id,
                f"Mapping into the graph stopped after {MAX_FAILURES_IN_A_ROW} failed calls in a "
                f"row ({state['stopped']}); the file can still be searched.",
            )
        elif state["failed"]:
            store.add_note(
                document_id, f"{state['failed']} passages couldn't be mapped into the graph."
            )

    # ------------------------------------------------------------------ briefing

    async def brief(
        self, base_id: str, query: str, provider_for: Callable[[ModelRef], Provider] | None = None
    ) -> KnowledgeBrief:
        """What the knowledge base has on `query`. With an embedding model (and a way to reach
        it), passages and entities are also found by meaning; if that fails, by words only."""
        query = query[-QUERY_CHARS:]
        embedding, _ = self.store.embedding_of(base_id)
        vector = None
        if embedding is not None:
            opened = None
            try:
                provider = (
                    provider_for(embedding) if provider_for else self._factory(embedding.provider)
                )
                opened = None if provider_for else provider
                if isinstance(provider, Embedder):
                    vector = (await provider.embed(embedding.model, [query]))[0]
            except ProviderError as exc:
                log.warning("query embedding failed for %s: %s", base_id, exc)
            finally:
                if opened is not None:
                    await opened.aclose()
        return search(self.store, base_id, query, vector)
