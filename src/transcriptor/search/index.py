"""Índice local (SQLite) de la bóveda: notas, fragmentos con FTS5 + vectores, y tareas.

Vive fuera de la carpeta compartida (evita corrupción por sincronización) y se puede
reconstruir en cualquier momento desde los archivos .md.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from transcriptor.nlp.embed import TextEmbedder
from transcriptor.nlp.text import fold
from transcriptor.session.models import TaskItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,            -- clase | tema | profesor | curso | indice | usuario
    owned INTEGER NOT NULL DEFAULT 0,
    course TEXT, day TEXT, session_id TEXT,
    aliases TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    mtime REAL NOT NULL DEFAULT 0,
    vector BLOB
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,            -- resumen | punto | transcripcion | nota
    t REAL, speaker TEXT,
    text TEXT NOT NULL,
    vector BLOB
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    session_id TEXT, course TEXT, note_path TEXT,
    description TEXT NOT NULL, due TEXT, due_text TEXT, t REAL,
    done INTEGER NOT NULL DEFAULT 0, confirmed INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL DEFAULT 'tarea',
    google_task_id TEXT, google_event_id TEXT, canvas_match TEXT, canvas_due TEXT
);
CREATE INDEX IF NOT EXISTS tasks_due ON tasks(done, due);
"""


@dataclass
class NoteRow:
    id: int
    path: str
    title: str
    kind: str
    owned: bool
    course: str | None
    day: str | None
    session_id: str | None
    aliases: list[str]
    summary: str
    vector: np.ndarray | None


@dataclass
class Hit:
    chunk_id: int
    note_path: str
    note_title: str
    course: str | None
    day: str | None
    kind: str
    t: float | None
    speaker: str | None
    text: str
    score: float


def _vec(blob: bytes | None) -> np.ndarray | None:
    return None if blob is None else np.frombuffer(blob, dtype=np.float32)


def _blob(vec: np.ndarray | None) -> bytes | None:
    return None if vec is None else np.asarray(vec, dtype=np.float32).tobytes()


class SearchIndex:
    def __init__(self, path: Path, embedder: TextEmbedder) -> None:
        self.path = path
        self.embedder = embedder
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self._check_embedder()

    def _check_embedder(self) -> None:
        row = self._conn.execute("SELECT value FROM meta WHERE key='embedder'").fetchone()
        if row and row[0] != self.embedder.name:
            # Cambió el modelo de embeddings: los vectores viejos no son comparables.
            self._conn.execute("UPDATE notes SET vector = NULL")
            self._conn.execute("UPDATE chunks SET vector = NULL")
            self._conn.execute("UPDATE notes SET mtime = 0")
        self._conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('embedder', ?)", (self.embedder.name,)
        )
        self._conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ notas
    def note_mtime(self, path: str) -> float | None:
        row = self._conn.execute("SELECT mtime FROM notes WHERE path=?", (path,)).fetchone()
        return row[0] if row else None

    def upsert_note(
        self,
        *,
        path: str,
        title: str,
        kind: str,
        owned: bool,
        mtime: float,
        course: str | None = None,
        day: str | None = None,
        session_id: str | None = None,
        aliases: Iterable[str] = (),
        summary: str = "",
        chunks: Sequence[tuple[str, float | None, str | None, str]] = (),
    ) -> int:
        """Inserta o reemplaza una nota con sus fragmentos (kind, t, hablante, texto)."""
        texts = [f"{title}. {summary}"] + [c[3] for c in chunks]
        vecs = self.embedder.embed(texts) if texts else np.zeros((0, self.embedder.dim))
        with self.tx() as db:
            row = db.execute("SELECT id FROM notes WHERE path=?", (path,)).fetchone()
            if row:
                note_id = row[0]
                db.execute("DELETE FROM chunks WHERE note_id=?", (note_id,))
                db.execute(
                    "UPDATE notes SET title=?, kind=?, owned=?, course=?, day=?, session_id=?, "
                    "aliases=?, summary=?, mtime=?, vector=? WHERE id=?",
                    (
                        title,
                        kind,
                        int(owned),
                        course,
                        day,
                        session_id,
                        json.dumps(list(aliases)),
                        summary,
                        mtime,
                        _blob(vecs[0]),
                        note_id,
                    ),
                )
            else:
                cur = db.execute(
                    "INSERT INTO notes (path, title, kind, owned, course, day, session_id, aliases, "
                    "summary, mtime, vector) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        path,
                        title,
                        kind,
                        int(owned),
                        course,
                        day,
                        session_id,
                        json.dumps(list(aliases)),
                        summary,
                        mtime,
                        _blob(vecs[0]),
                    ),
                )
                note_id = int(cur.lastrowid or 0)
            for (ckind, t, speaker, text), vec in zip(chunks, vecs[1:], strict=True):
                db.execute(
                    "INSERT INTO chunks (note_id, kind, t, speaker, text, vector) VALUES (?,?,?,?,?,?)",
                    (note_id, ckind, t, speaker, text, _blob(vec)),
                )
        return note_id

    def remove_note(self, path: str) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM notes WHERE path=?", (path,))

    def rename_note(self, old: str, new: str) -> None:
        with self.tx() as db:
            db.execute("UPDATE notes SET path=? WHERE path=?", (new, old))

    def notes(self, kind: str | None = None, course: str | None = None) -> list[NoteRow]:
        sql = "SELECT * FROM notes WHERE 1=1"
        args: list[object] = []
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if course:
            sql += " AND course=?"
            args.append(course)
        rows = self._conn.execute(sql + " ORDER BY day, path", args).fetchall()
        return [
            NoteRow(
                r["id"],
                r["path"],
                r["title"],
                r["kind"],
                bool(r["owned"]),
                r["course"],
                r["day"],
                r["session_id"],
                json.loads(r["aliases"]),
                r["summary"],
                _vec(r["vector"]),
            )
            for r in rows
        ]

    def all_paths(self) -> set[str]:
        return {r[0] for r in self._conn.execute("SELECT path FROM notes")}

    def count_notes(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0])

    # ------------------------------------------------------------------ búsqueda
    def _fts_query(self, query: str) -> str:
        terms = [t for t in fold(query).replace('"', " ").split() if len(t) > 2]
        return " OR ".join(f'"{t}"*' for t in terms)

    def search(
        self,
        query: str,
        k: int = 6,
        course: str | None = None,
        since: date | None = None,
        kinds: tuple[str, ...] | None = None,
    ) -> list[Hit]:
        """Búsqueda híbrida: palabras exactas (FTS5/BM25) + similitud semántica, fusionadas por RRF."""
        filters = " AND 1=1"
        args: list[object] = []
        if course:
            filters += " AND n.course=?"
            args.append(course)
        if since:
            filters += " AND n.day>=?"
            args.append(since.isoformat())
        if kinds:
            filters += f" AND c.kind IN ({','.join('?' * len(kinds))})"
            args.extend(kinds)

        scores: dict[int, float] = {}
        fts = self._fts_query(query)
        if fts:
            rows = self._conn.execute(
                "SELECT c.id FROM chunks_fts f JOIN chunks c ON c.id=f.rowid JOIN notes n ON n.id=c.note_id "
                f"WHERE chunks_fts MATCH ?{filters} ORDER BY bm25(chunks_fts) LIMIT 50",
                [fts, *args],
            ).fetchall()
            for rank, r in enumerate(rows):
                scores[r[0]] = scores.get(r[0], 0) + 1 / (60 + rank)

        qvec = self.embedder.embed([query])[0]
        rows = self._conn.execute(
            f"SELECT c.id, c.vector FROM chunks c JOIN notes n ON n.id=c.note_id WHERE c.vector IS NOT NULL{filters}",
            args,
        ).fetchall()
        if rows:
            ids = [r[0] for r in rows]
            mat = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
            sims = mat @ qvec
            order = np.argsort(-sims)[:50]
            for rank, idx in enumerate(order):
                if sims[idx] <= 0.05:
                    break
                scores[ids[idx]] = scores.get(ids[idx], 0) + 1 / (60 + rank)

        best = sorted(scores.items(), key=lambda x: -x[1])[:k]
        hits = []
        for chunk_id, score in best:
            r = self._conn.execute(
                "SELECT c.*, n.path, n.title, n.course, n.day FROM chunks c JOIN notes n ON n.id=c.note_id WHERE c.id=?",
                (chunk_id,),
            ).fetchone()
            hits.append(
                Hit(
                    chunk_id,
                    r["path"],
                    r["title"],
                    r["course"],
                    r["day"],
                    r["kind"],
                    r["t"],
                    r["speaker"],
                    r["text"],
                    score,
                )
            )
        return hits

    def similar_notes(
        self,
        vector: np.ndarray,
        kind: str | None = None,
        exclude: Iterable[str] = (),
        k: int = 5,
        min_score: float = 0.0,
    ) -> list[tuple[NoteRow, float]]:
        excluded = set(exclude)
        candidates = [
            n for n in self.notes(kind) if n.vector is not None and n.path not in excluded
        ]
        if not candidates:
            return []
        mat = np.vstack([n.vector for n in candidates if n.vector is not None])
        sims = mat @ vector
        order = np.argsort(-sims)
        return [(candidates[i], float(sims[i])) for i in order[:k] if sims[i] >= min_score]

    def doc_freq(self, phrase: str) -> int:
        q = self._fts_query(phrase)
        if not q:
            return 0
        q = " AND ".join(f'"{t}"' for t in fold(phrase).split() if len(t) > 2) or q
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT c.note_id) FROM chunks_fts f JOIN chunks c ON c.id=f.rowid WHERE chunks_fts MATCH ?",
            (q,),
        ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------ tareas
    def upsert_tasks(
        self, tasks: list[TaskItem], *, session_id: str, course: str, note_path: str
    ) -> None:
        with self.tx() as db:
            db.execute(
                "DELETE FROM tasks WHERE session_id=? AND id NOT IN (%s)"
                % ",".join("?" * len(tasks)),
                [session_id, *[t.id for t in tasks]],
            )
            for t in tasks:
                db.execute(
                    "INSERT OR REPLACE INTO tasks (id, session_id, course, note_path, description, due, "
                    "due_text, t, done, confirmed, kind, google_task_id, google_event_id, canvas_match, "
                    "canvas_due) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        t.id,
                        session_id,
                        course,
                        note_path,
                        t.description,
                        t.due.isoformat() if t.due else None,
                        t.due_text,
                        t.t,
                        int(t.done),
                        int(t.confirmed),
                        t.kind,
                        t.google_task_id,
                        t.google_event_id,
                        t.canvas_match,
                        t.canvas_due.isoformat() if t.canvas_due else None,
                    ),
                )

    def tasks(
        self,
        pending_only: bool = True,
        course: str | None = None,
        until: date | None = None,
        since: date | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        args: list[object] = []
        if pending_only:
            sql += " AND done=0"
        if course:
            sql += " AND course=?"
            args.append(course)
        if until:
            sql += " AND (due IS NULL OR due<=?)"
            args.append(until.isoformat())
        if since:
            sql += " AND (due IS NULL OR due>=?)"
            args.append(since.isoformat())
        sql += " ORDER BY due IS NULL, due, course"
        return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def set_task_done(self, task_id: str, done: bool) -> None:
        with self.tx() as db:
            db.execute("UPDATE tasks SET done=? WHERE id=?", (int(done), task_id))

    def courses(self) -> list[str]:
        return [
            r[0]
            for r in self._conn.execute(
                "SELECT DISTINCT course FROM notes WHERE kind='clase' AND course IS NOT NULL ORDER BY course"
            )
        ]
