"""SQLite with FTS5 for full-text search over the codex markdown."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
    title,
    body,
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS doc_fts_map (
    doc_id INTEGER PRIMARY KEY,
    fts_rowid INTEGER NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def cursor(conn: sqlite3.Connection):
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    finally:
        cur.close()


def upsert_document(conn: sqlite3.Connection, *, path: str, kind: str,
                    title: str, body: str, updated_at: str) -> None:
    with cursor(conn) as cur:
        cur.execute(
            "INSERT INTO documents(path, kind, title, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET kind=excluded.kind, "
            "title=excluded.title, updated_at=excluded.updated_at",
            (path, kind, title, updated_at),
        )
        cur.execute("SELECT id FROM documents WHERE path = ?", (path,))
        doc_id = cur.fetchone()["id"]

        cur.execute("SELECT fts_rowid FROM doc_fts_map WHERE doc_id = ?", (doc_id,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE doc_fts SET title = ?, body = ? WHERE rowid = ?",
                        (title, body, row["fts_rowid"]))
        else:
            cur.execute("INSERT INTO doc_fts(title, body) VALUES(?, ?)", (title, body))
            cur.execute("INSERT INTO doc_fts_map(doc_id, fts_rowid) VALUES(?, ?)",
                        (doc_id, cur.lastrowid))


def search(conn: sqlite3.Connection, query: str, limit: int = 25) -> list[dict]:
    sql = """
    SELECT d.path, d.kind, d.title,
           snippet(doc_fts, 1, '<mark>', '</mark>', '...', 16) AS snippet,
           bm25(doc_fts) AS score
    FROM doc_fts
    JOIN doc_fts_map m ON m.fts_rowid = doc_fts.rowid
    JOIN documents d ON d.id = m.doc_id
    WHERE doc_fts MATCH ?
    ORDER BY score
    LIMIT ?
    """
    with cursor(conn) as cur:
        cur.execute(sql, (query, limit))
        return [dict(row) for row in cur.fetchall()]


def list_documents(conn: sqlite3.Connection, kind: str | None = None) -> list[dict]:
    sql = "SELECT path, kind, title, updated_at FROM documents"
    params: tuple = ()
    if kind:
        sql += " WHERE kind = ?"
        params = (kind,)
    sql += " ORDER BY updated_at DESC"
    with cursor(conn) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
