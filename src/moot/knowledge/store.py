"""Agents' knowledge on this computer: one SQLite file (no server) with each knowledge base's
files, their passages (with a keyword index and, with an embedding model, vectors), and the
knowledge graph built from them: entities, the facts linking them, and where each is
mentioned. Deleting a file removes everything that came from it.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from moot.core.base import MootModel, new_id, utc_now
from moot.core.persona import ModelRef

SCHEMA_VERSION = 1
KNOWLEDGE_DB = "knowledge.db"

_SCHEMA = """
CREATE TABLE bases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    embed_provider TEXT,
    embed_model TEXT,
    dims INTEGER
);
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    base_id TEXT NOT NULL REFERENCES bases (id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    chunks_total INTEGER NOT NULL DEFAULT 0,
    chunks_mapped INTEGER NOT NULL DEFAULT 0,
    chunks_failed INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    base_id TEXT NOT NULL REFERENCES bases (id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    page INTEGER,
    text TEXT NOT NULL,
    vector BLOB
);
CREATE INDEX chunks_by_base ON chunks (base_id);
CREATE TABLE entities (
    id INTEGER PRIMARY KEY,
    base_id TEXT NOT NULL REFERENCES bases (id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    vector BLOB,
    UNIQUE (base_id, key)
);
CREATE TABLE mentions (
    entity_id INTEGER NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL REFERENCES chunks (id) ON DELETE CASCADE,
    PRIMARY KEY (entity_id, chunk_id)
);
CREATE INDEX mentions_by_chunk ON mentions (chunk_id);
CREATE TABLE facts (
    id INTEGER PRIMARY KEY,
    base_id TEXT NOT NULL REFERENCES bases (id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL REFERENCES chunks (id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    object_id INTEGER REFERENCES entities (id) ON DELETE CASCADE,
    object_text TEXT NOT NULL DEFAULT '',
    relation TEXT NOT NULL DEFAULT '',
    statement TEXT NOT NULL,
    vector BLOB
);
CREATE INDEX facts_by_subject ON facts (subject_id);
CREATE INDEX facts_by_object ON facts (object_id);
"""

_FTS = """
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    text, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER chunks_added AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts (rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER chunks_removed AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts (chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""

STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from has have how if in into is it its "
    "not of on or our should so than that the their them then there these they this to was "
    "we were what when where which who why will with would you your".split()
)


class KnowledgeError(ValueError):
    """A knowledge base or file that doesn't exist, or a change that isn't possible."""


class DocumentInfo(MootModel):
    id: str
    filename: str
    bytes: int
    created_at: datetime
    status: str  # queued, indexing, ready, failed
    stage: str = ""  # reading, embedding, mapping, linking
    chunks_total: int = 0
    chunks_mapped: int = 0
    chunks_failed: int = 0
    error: str | None = None
    notes: list[str] = []


class BaseInfo(MootModel):
    id: str
    name: str
    created_at: datetime
    embedding: ModelRef | None = None
    documents: list[DocumentInfo] = []
    passages: int = 0
    entities: int = 0
    facts: int = 0


@dataclass
class ChunkRow:
    id: int
    document_id: str
    filename: str
    page: int | None
    text: str


@dataclass
class FactRow:
    id: int
    chunk_id: int
    subject_id: int
    object_id: int | None
    subject: str
    relation: str
    object: str
    statement: str


def _vector_bytes(vector: Iterable[float]) -> bytes:
    array = np.asarray(list(vector), dtype=np.float32)
    norm = float(np.linalg.norm(array))
    return (array / norm if norm else array).tobytes()


def keyword_terms(text: str, limit: int = 24) -> list[str]:
    words = re.findall(r"[^\W_]{3,}", text.casefold())
    terms: list[str] = []
    for word in words:
        if word not in STOPWORDS and word not in terms:
            terms.append(word)
    return terms[:limit]


class KnowledgeStore:
    """The knowledge bases. `path` is the database file (":memory:" for tests)."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = NORMAL")
        self._migrate()
        self.has_fts = bool(
            self._db.execute("SELECT 1 FROM sqlite_master WHERE name = 'chunks_fts'").fetchone()
        )
        self._vectors: dict[tuple[str, str], tuple[list[int], np.ndarray]] = {}

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise KnowledgeError(
                f"The knowledge in {self.path} was saved by a newer version of Moot. "
                "Update Moot, or start it with a different data folder."
            )
        if version == 0:
            with self._db:
                self._db.executescript(_SCHEMA)
                try:
                    self._db.executescript(_FTS)
                except sqlite3.OperationalError:
                    pass  # this SQLite has no FTS5: keyword search falls back to a scan
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------------ bases

    def create_base(self, name: str, embedding: ModelRef | None = None) -> BaseInfo:
        base_id = new_id("kb")
        with self._db:
            self._db.execute(
                "INSERT INTO bases (id, name, created_at, embed_provider, embed_model)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    base_id,
                    " ".join(name.split())[:80] or "Knowledge",
                    utc_now().isoformat(),
                    embedding.provider if embedding else None,
                    embedding.model if embedding else None,
                ),
            )
        return self.base(base_id)

    def exists(self, base_id: str) -> bool:
        return (
            self._db.execute("SELECT 1 FROM bases WHERE id = ?", (base_id,)).fetchone() is not None
        )

    def base(self, base_id: str) -> BaseInfo:
        row = self._db.execute(
            "SELECT id, name, created_at, embed_provider, embed_model FROM bases WHERE id = ?",
            (base_id,),
        ).fetchone()
        if row is None:
            raise KnowledgeError(f"No knowledge base {base_id}")
        count = lambda table: self._db.execute(  # noqa: E731
            f"SELECT COUNT(*) FROM {table} WHERE base_id = ?", (base_id,)
        ).fetchone()[0]
        return BaseInfo(
            id=row[0],
            name=row[1],
            created_at=row[2],
            embedding=ModelRef(provider=row[3], model=row[4]) if row[3] and row[4] else None,
            documents=self.documents(base_id),
            passages=count("chunks"),
            entities=count("entities"),
            facts=count("facts"),
        )

    def bases(self) -> list[BaseInfo]:
        ids = [r[0] for r in self._db.execute("SELECT id FROM bases ORDER BY created_at DESC")]
        return [self.base(base_id) for base_id in ids]

    def delete_base(self, base_id: str) -> bool:
        with self._db:
            deleted = self._db.execute("DELETE FROM bases WHERE id = ?", (base_id,)).rowcount > 0
        self._forget_vectors(base_id)
        return deleted

    def embedding_of(self, base_id: str) -> tuple[ModelRef | None, int | None]:
        row = self._db.execute(
            "SELECT embed_provider, embed_model, dims FROM bases WHERE id = ?", (base_id,)
        ).fetchone()
        if row is None:
            raise KnowledgeError(f"No knowledge base {base_id}")
        ref = ModelRef(provider=row[0], model=row[1]) if row[0] and row[1] else None
        return ref, row[2]

    def set_dims(self, base_id: str, dims: int) -> None:
        with self._db:
            self._db.execute("UPDATE bases SET dims = ? WHERE id = ?", (dims, base_id))

    # ------------------------------------------------------------------ documents

    def add_document(self, base_id: str, filename: str, size: int) -> DocumentInfo:
        if not self.exists(base_id):
            raise KnowledgeError(f"No knowledge base {base_id}")
        document_id = new_id("doc")
        with self._db:
            self._db.execute(
                "INSERT INTO documents (id, base_id, filename, bytes, created_at, status)"
                " VALUES (?, ?, ?, ?, ?, 'queued')",
                (document_id, base_id, filename, size, utc_now().isoformat()),
            )
        return self.document(document_id)

    def document(self, document_id: str) -> DocumentInfo:
        row = self._db.execute(
            "SELECT id, filename, bytes, created_at, status, stage, chunks_total, chunks_mapped,"
            " chunks_failed, error, notes FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            raise KnowledgeError(f"No file {document_id}")
        return self._document(row)

    def documents(self, base_id: str) -> list[DocumentInfo]:
        rows = self._db.execute(
            "SELECT id, filename, bytes, created_at, status, stage, chunks_total, chunks_mapped,"
            " chunks_failed, error, notes FROM documents WHERE base_id = ? ORDER BY created_at",
            (base_id,),
        )
        return [self._document(row) for row in rows]

    @staticmethod
    def _document(row: tuple) -> DocumentInfo:
        return DocumentInfo(
            id=row[0],
            filename=row[1],
            bytes=row[2],
            created_at=row[3],
            status=row[4],
            stage=row[5],
            chunks_total=row[6],
            chunks_mapped=row[7],
            chunks_failed=row[8],
            error=row[9],
            notes=[n for n in row[10].split("\n") if n],
        )

    def document_base(self, document_id: str) -> str:
        row = self._db.execute(
            "SELECT base_id FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if row is None:
            raise KnowledgeError(f"No file {document_id}")
        return row[0]

    def update_document(self, document_id: str, **fields: object) -> None:
        allowed = {"status", "stage", "chunks_total", "chunks_mapped", "chunks_failed", "error"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        columns = ", ".join(f"{k} = ?" for k in sets)
        with self._db:
            self._db.execute(
                f"UPDATE documents SET {columns} WHERE id = ?", (*sets.values(), document_id)
            )

    def add_note(self, document_id: str, note: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE documents SET notes = notes || ? || char(10) WHERE id = ?",
                (note, document_id),
            )

    def delete_document(self, document_id: str) -> bool:
        """Remove a file and everything learned from it, keeping entities other files mention."""
        base_id = self.document_base(document_id)
        with self._db:
            deleted = (
                self._db.execute("DELETE FROM documents WHERE id = ?", (document_id,)).rowcount > 0
            )
            self._db.execute(
                "DELETE FROM entities WHERE base_id = ? AND id NOT IN"
                " (SELECT entity_id FROM mentions)",
                (base_id,),
            )
        self._forget_vectors(base_id)
        return deleted

    def interrupted(self) -> list[str]:
        """Files still being indexed when Moot stopped."""
        rows = self._db.execute("SELECT id FROM documents WHERE status IN ('queued', 'indexing')")
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ passages

    def add_chunks(
        self, base_id: str, document_id: str, chunks: list[tuple[int, int | None, str]]
    ) -> list[int]:
        """Save passages (index, page, text) and return their ids."""
        ids = []
        with self._db:
            for index, page, text in chunks:
                cursor = self._db.execute(
                    "INSERT INTO chunks (base_id, document_id, idx, page, text)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (base_id, document_id, index, page, text),
                )
                ids.append(int(cursor.lastrowid))
        return ids

    def set_chunk_vectors(self, base_id: str, pairs: list[tuple[int, list[float]]]) -> None:
        with self._db:
            self._db.executemany(
                "UPDATE chunks SET vector = ? WHERE id = ?",
                [(_vector_bytes(vector), chunk_id) for chunk_id, vector in pairs],
            )
        self._forget_vectors(base_id)

    def chunks(self, chunk_ids: Iterable[int]) -> dict[int, ChunkRow]:
        ids = list(chunk_ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self._db.execute(
            "SELECT c.id, c.document_id, d.filename, c.page, c.text FROM chunks c"
            f" JOIN documents d ON d.id = c.document_id WHERE c.id IN ({marks})",
            ids,
        )
        return {r[0]: ChunkRow(r[0], r[1], r[2], r[3], r[4]) for r in rows}

    def keyword_chunks(self, base_id: str, text: str, limit: int = 20) -> list[int]:
        """Passages matching the words in `text`, best first."""
        terms = keyword_terms(text)
        if not terms:
            return []
        if self.has_fts:
            query = " OR ".join(f'"{term}"' for term in terms)
            rows = self._db.execute(
                "SELECT chunks_fts.rowid FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
                " WHERE chunks_fts MATCH ? AND c.base_id = ? ORDER BY bm25(chunks_fts) LIMIT ?",
                (query, base_id, limit),
            )
            return [r[0] for r in rows]
        scored = []
        for chunk_id, body in self._db.execute(
            "SELECT id, text FROM chunks WHERE base_id = ?", (base_id,)
        ):
            words = body.casefold()
            score = sum(1 for term in terms if term in words)
            if score:
                scored.append((score, chunk_id))
        return [chunk_id for _, chunk_id in sorted(scored, key=lambda s: (-s[0], s[1]))[:limit]]

    def similar(
        self, base_id: str, table: str, vector: list[float], limit: int
    ) -> list[tuple[int, float]]:
        """The rows of `table` (chunks or entities) closest in meaning, with their similarity."""
        ids, matrix = self._matrix(base_id, table)
        if not ids:
            return []
        query = np.frombuffer(_vector_bytes(vector), dtype=np.float32)
        if query.shape[0] != matrix.shape[1]:
            return []
        scores = matrix @ query
        best = np.argsort(-scores)[:limit]
        return [(ids[i], float(scores[i])) for i in best]

    def similarity_of(
        self, base_id: str, table: str, vector: list[float], ids: Iterable[int]
    ) -> dict[int, float]:
        """How close in meaning each of the given rows is (rows without a vector are left out)."""
        all_ids, matrix = self._matrix(base_id, table)
        if not all_ids:
            return {}
        query = np.frombuffer(_vector_bytes(vector), dtype=np.float32)
        if query.shape[0] != matrix.shape[1]:
            return {}
        position = {row_id: i for i, row_id in enumerate(all_ids)}
        wanted = [row_id for row_id in ids if row_id in position]
        if not wanted:
            return {}
        scores = matrix[[position[row_id] for row_id in wanted]] @ query
        return {row_id: float(score) for row_id, score in zip(wanted, scores, strict=True)}

    def _matrix(self, base_id: str, table: str) -> tuple[list[int], np.ndarray]:
        key = (base_id, table)
        if key not in self._vectors:
            assert table in ("chunks", "entities", "facts")
            rows = self._db.execute(
                f"SELECT id, vector FROM {table} WHERE base_id = ? AND vector IS NOT NULL",
                (base_id,),
            ).fetchall()
            sizes = {len(r[1]) for r in rows}
            if not rows or len(sizes) > 1:
                self._vectors[key] = ([], np.zeros((0, 0), dtype=np.float32))
            else:
                matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32)
                self._vectors[key] = ([r[0] for r in rows], matrix.reshape(len(rows), -1))
        return self._vectors[key]

    def _forget_vectors(self, base_id: str) -> None:
        for key in [k for k in self._vectors if k[0] == base_id]:
            del self._vectors[key]

    # ------------------------------------------------------------------ the graph

    def upsert_entity(self, base_id: str, key: str, name: str, type_: str, description: str) -> int:
        row = self._db.execute(
            "SELECT id, description, type FROM entities WHERE base_id = ? AND key = ?",
            (base_id, key),
        ).fetchone()
        with self._db:
            if row is None:
                cursor = self._db.execute(
                    "INSERT INTO entities (base_id, key, name, type, description)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (base_id, key, name, type_, description),
                )
                return int(cursor.lastrowid)
            if (not row[1] and description) or (not row[2] and type_):
                self._db.execute(
                    "UPDATE entities SET description = ?, type = ? WHERE id = ?",
                    (row[1] or description, row[2] or type_, row[0]),
                )
            return int(row[0])

    def add_mentions(self, chunk_id: int, entity_ids: Iterable[int]) -> None:
        with self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO mentions (entity_id, chunk_id) VALUES (?, ?)",
                [(entity_id, chunk_id) for entity_id in entity_ids],
            )

    def add_fact(
        self,
        base_id: str,
        chunk_id: int,
        subject_id: int,
        object_id: int | None,
        object_text: str,
        relation: str,
        statement: str,
    ) -> int:
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO facts (base_id, chunk_id, subject_id, object_id, object_text,"
                " relation, statement) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (base_id, chunk_id, subject_id, object_id, object_text, relation, statement),
            )
        return int(cursor.lastrowid)

    def entities_without_vectors(self, base_id: str) -> list[tuple[int, str]]:
        rows = self._db.execute(
            "SELECT id, name, description FROM entities WHERE base_id = ? AND vector IS NULL",
            (base_id,),
        )
        return [(r[0], f"{r[1]}: {r[2]}" if r[2] else r[1]) for r in rows]

    def set_entity_vectors(self, base_id: str, pairs: list[tuple[int, list[float]]]) -> None:
        with self._db:
            self._db.executemany(
                "UPDATE entities SET vector = ? WHERE id = ?",
                [(_vector_bytes(vector), entity_id) for entity_id, vector in pairs],
            )
        self._forget_vectors(base_id)

    def facts_without_vectors(self, base_id: str) -> list[tuple[int, str]]:
        rows = self._db.execute(
            "SELECT id, statement FROM facts WHERE base_id = ? AND vector IS NULL", (base_id,)
        )
        return [(r[0], r[1]) for r in rows]

    def set_fact_vectors(self, base_id: str, pairs: list[tuple[int, list[float]]]) -> None:
        with self._db:
            self._db.executemany(
                "UPDATE facts SET vector = ? WHERE id = ?",
                [(_vector_bytes(vector), fact_id) for fact_id, vector in pairs],
            )
        self._forget_vectors(base_id)

    def entity_keys(self, base_id: str) -> dict[str, int]:
        return {
            key: entity_id
            for entity_id, key in self._db.execute(
                "SELECT id, key FROM entities WHERE base_id = ?", (base_id,)
            )
        }

    def entities_in_chunks(self, chunk_ids: Iterable[int]) -> dict[int, list[int]]:
        ids = list(chunk_ids)
        found: dict[int, list[int]] = {}
        if not ids:
            return found
        marks = ",".join("?" * len(ids))
        for entity_id, chunk_id in self._db.execute(
            f"SELECT entity_id, chunk_id FROM mentions WHERE chunk_id IN ({marks})", ids
        ):
            found.setdefault(chunk_id, []).append(entity_id)
        return found

    _FACT_COLUMNS = (
        "SELECT f.id, f.chunk_id, f.subject_id, f.object_id, s.name, f.relation,"
        " COALESCE(o.name, f.object_text), f.statement FROM facts f"
        " JOIN entities s ON s.id = f.subject_id LEFT JOIN entities o ON o.id = f.object_id"
    )

    def facts_about(self, entity_ids: Iterable[int], limit: int = 200) -> list[FactRow]:
        ids = list(entity_ids)
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self._db.execute(
            f"{self._FACT_COLUMNS} WHERE f.subject_id IN ({marks}) OR f.object_id IN ({marks})"
            " LIMIT ?",
            (*ids, *ids, limit),
        )
        return [FactRow(*row) for row in rows]

    def facts(self, fact_ids: Iterable[int]) -> list[FactRow]:
        ids = list(fact_ids)
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self._db.execute(f"{self._FACT_COLUMNS} WHERE f.id IN ({marks})", ids)
        return [FactRow(*row) for row in rows]

    def graph(self, base_id: str, limit: int = 60) -> tuple[list[dict], list[dict]]:
        """The most connected entities and the facts between them (for a preview)."""
        entities = [
            {"id": r[0], "name": r[1], "type": r[2], "description": r[3], "links": r[4]}
            for r in self._db.execute(
                "SELECT e.id, e.name, e.type, e.description,"
                " (SELECT COUNT(*) FROM facts f WHERE f.subject_id = e.id OR f.object_id = e.id)"
                " AS links FROM entities e WHERE e.base_id = ? ORDER BY links DESC, e.id LIMIT ?",
                (base_id, limit),
            )
        ]
        shown = [e["id"] for e in entities]
        facts = [
            {
                "subject": f.subject,
                "relation": f.relation,
                "object": f.object,
                "statement": f.statement,
            }
            for f in self.facts_about(shown, limit=limit * 2)
        ]
        return entities, facts
