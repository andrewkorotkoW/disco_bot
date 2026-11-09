"""SQLite-хранилище кейсов и поиск похожих (эмбеддинги + FTS5 фолбэк)."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import search

DEFAULT_DB_PATH = "data/disco_bot.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    source_channel_id INTEGER,
    source_message_id INTEGER,
    player_name TEXT,
    player_id INTEGER,
    ticket_text TEXT NOT NULL,
    category TEXT,
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved'))
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    admin_id INTEGER NOT NULL,
    decision_text TEXT NOT NULL,
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    case_id INTEGER PRIMARY KEY REFERENCES cases(id),
    vector BLOB NOT NULL,
    model_name TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
    case_id UNINDEXED,
    ticket_text,
    summary
);
"""


@dataclass
class Case:
    id: int
    created_at: str
    source_channel_id: Optional[int]
    source_message_id: Optional[int]
    player_name: Optional[str]
    player_id: Optional[int]
    ticket_text: str
    category: Optional[str]
    summary: Optional[str]
    status: str


@dataclass
class Decision:
    case_id: int
    admin_id: int
    decision_text: str
    decided_at: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_case(row: sqlite3.Row) -> Case:
    return Case(
        id=row["id"],
        created_at=row["created_at"],
        source_channel_id=row["source_channel_id"],
        source_message_id=row["source_message_id"],
        player_name=row["player_name"],
        player_id=row["player_id"],
        ticket_text=row["ticket_text"],
        category=row["category"],
        summary=row["summary"],
        status=row["status"],
    )


def _row_to_decision(row: sqlite3.Row) -> Decision:
    return Decision(
        case_id=row["case_id"],
        admin_id=row["admin_id"],
        decision_text=row["decision_text"],
        decided_at=row["decided_at"],
    )


class CaseStore:
    """Хранилище кейсов в SQLite. Путь к БД — аргумент конструктора (или DB_PATH из env)."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.environ.get("DB_PATH", DEFAULT_DB_PATH)
        self.db_path = db_path
        if db_path != ":memory:":
            parent = Path(db_path).parent
            if str(parent) not in ("", "."):
                parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CaseStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def add_case(
        self,
        ticket_text: str,
        *,
        source_channel_id: Optional[int] = None,
        source_message_id: Optional[int] = None,
        player_name: Optional[str] = None,
        player_id: Optional[int] = None,
        category: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> Case:
        created_at = _utcnow()
        cur = self._conn.execute(
            """
            INSERT INTO cases
                (created_at, source_channel_id, source_message_id, player_name,
                 player_id, ticket_text, category, summary, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                created_at,
                source_channel_id,
                source_message_id,
                player_name,
                player_id,
                ticket_text,
                category,
                summary,
            ),
        )
        case_id = cur.lastrowid
        self._conn.execute(
            "INSERT INTO cases_fts (case_id, ticket_text, summary) VALUES (?, ?, ?)",
            (case_id, ticket_text, summary or ""),
        )
        self._conn.commit()
        return self.get_case(case_id)

    def get_case(self, case_id: int) -> Optional[Case]:
        row = self._conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return _row_to_case(row) if row else None

    def _latest_decision(self, case_id: int) -> Optional[Decision]:
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE case_id = ? ORDER BY id DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        return _row_to_decision(row) if row else None

    def get_decision(self, case_id: int) -> Optional[Decision]:
        """Последнее решение по кейсу, если оно уже принято."""
        return self._latest_decision(case_id)

    def resolve_case(self, case_id: int, admin_id: int, decision_text: str) -> Decision:
        case = self.get_case(case_id)
        if case is None:
            raise ValueError(f"case {case_id} not found")

        decided_at = _utcnow()
        self._conn.execute(
            """
            INSERT INTO decisions (case_id, admin_id, decision_text, decided_at)
            VALUES (?, ?, ?, ?)
            """,
            (case_id, admin_id, decision_text, decided_at),
        )
        self._conn.execute("UPDATE cases SET status = 'resolved' WHERE id = ?", (case_id,))

        embedding_text = f"{case.ticket_text} {case.summary or ''}".strip()
        try:
            vector = search.embed_text(embedding_text)
        except Exception:
            vector = None

        if vector is not None:
            self._conn.execute(
                """
                INSERT INTO embeddings (case_id, vector, model_name) VALUES (?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET vector = excluded.vector, model_name = excluded.model_name
                """,
                (case_id, search.vector_to_blob(vector), search.EMBEDDING_MODEL_NAME),
            )

        self._conn.commit()
        return Decision(
            case_id=case_id, admin_id=admin_id, decision_text=decision_text, decided_at=decided_at
        )

    def find_similar(
        self, text: str, k: int = 5, category: Optional[str] = None
    ) -> list[tuple[Case, Optional[Decision], float, str]]:
        try:
            return self._find_similar_embedding(text, k, category)
        except Exception:
            return self._find_similar_fts(text, k, category)

    def _find_similar_embedding(
        self, text: str, k: int, category: Optional[str]
    ) -> list[tuple[Case, Optional[Decision], float, str]]:
        query_vector = search.embed_text(text)

        sql = "SELECT c.*, e.vector AS embedding_vector FROM embeddings e JOIN cases c ON c.id = e.case_id"
        params: list = []
        if category is not None:
            sql += " WHERE c.category = ?"
            params.append(category)

        scored = []
        for row in self._conn.execute(sql, params).fetchall():
            vector = search.blob_to_vector(row["embedding_vector"])
            score = search.cosine_similarity(query_vector, vector)
            scored.append((row, score))
        scored.sort(key=lambda item: item[1], reverse=True)

        results = []
        for row, score in scored[:k]:
            case = _row_to_case(row)
            results.append((case, self._latest_decision(case.id), score, "embedding"))
        return results

    def _find_similar_fts(
        self, text: str, k: int, category: Optional[str]
    ) -> list[tuple[Case, Optional[Decision], float, str]]:
        match_query = search.fts_match_query(text)
        sql = (
            "SELECT c.*, bm25(cases_fts) AS rank FROM cases_fts "
            "JOIN cases c ON c.id = cases_fts.case_id WHERE cases_fts MATCH ?"
        )
        params: list = [match_query]
        if category is not None:
            sql += " AND c.category = ?"
            params.append(category)
        sql += " ORDER BY rank LIMIT ?"
        params.append(k)

        results = []
        for row in self._conn.execute(sql, params).fetchall():
            case = _row_to_case(row)
            score = -row["rank"]  # bm25: меньше значит лучше, разворачиваем для "больше — лучше"
            results.append((case, self._latest_decision(case.id), score, "fts"))
        return results
