"""SQLite storage for traces."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

DEFAULT_DB = "llm_observe.db"
ENV_VAR = "LLM_OBSERVE_DB"

SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,     -- UTC start time, ISO 8601
    name          TEXT NOT NULL,     -- which function or client method was called
    model         TEXT,
    prompt        TEXT NOT NULL,
    response      TEXT,
    latency_ms    REAL NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL,
    error         TEXT               -- "ExceptionType: message", NULL on success
);
CREATE INDEX IF NOT EXISTS traces_ts ON traces (ts);
"""

FIELDS = (
    "ts",
    "name",
    "model",
    "prompt",
    "response",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "error",
)


def default_db_path() -> str:
    """``$LLM_OBSERVE_DB`` if set, otherwise ``llm_observe.db`` in the current directory."""
    return os.environ.get(ENV_VAR) or DEFAULT_DB


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_PROMPT_KEYS = ("content", "prompt", "input", "text", "query", "question")


def _last_prompt_string(node: object) -> str | None:
    """The last string stored under a prompt-like key anywhere in ``node``."""
    found = None
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _PROMPT_KEYS and isinstance(value, str):
                found = value
            else:
                found = _last_prompt_string(value) or found
    elif isinstance(node, list):
        for item in node:
            found = _last_prompt_string(item) or found
    return found


def preview(prompt: str | None, limit: int = 60) -> str:
    """A one-line summary of a stored prompt for tables. For structured
    prompts (keyword arguments, message lists) this is the last text found
    under a key such as ``content`` or ``prompt``, i.e. usually the latest
    user message."""
    text = prompt or ""
    if text[:1] in "{[":
        try:
            text = _last_prompt_string(json.loads(text)) or text
        except ValueError:
            pass
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class TraceStore:
    """One SQLite file of traces. Safe to share between threads."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = str(path) if path is not None else default_db_path()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add(self, **trace) -> int:
        values = [trace.get(field) for field in FIELDS]
        with self._lock, self._conn:
            cursor = self._conn.execute(
                f"INSERT INTO traces ({', '.join(FIELDS)}) VALUES ({', '.join('?' * len(FIELDS))})",
                values,
            )
            return int(cursor.lastrowid)

    def get(self, trace_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM traces WHERE id = ?", (trace_id,)).fetchone()
        return dict(row) if row else None

    def list(
        self,
        *,
        query: str | None = None,
        name: str | None = None,
        model: str | None = None,
        errors_only: bool = False,
        since: str | None = None,
        limit: int | None = 20,
    ) -> list[dict]:
        """Newest first. ``query`` is a case-insensitive substring match on the
        prompt, response and error text; ``since`` is an ISO date or timestamp."""
        where, params = [], []
        if query:
            where.append(
                "(prompt LIKE ? ESCAPE '\\' OR response LIKE ? ESCAPE '\\' OR error LIKE ? ESCAPE '\\')"
            )
            params += [_like(query)] * 3
        if name:
            where.append("name = ?")
            params.append(name)
        if model:
            where.append("model = ?")
            params.append(model)
        if errors_only:
            where.append("error IS NOT NULL")
        if since:
            where.append("ts >= ?")
            params.append(since)
        sql = "SELECT * FROM traces"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params)]

    def stats(self) -> list[dict]:
        """Per-name totals: calls, errors, average and maximum latency, tokens, cost."""
        sql = """
            SELECT name,
                   COUNT(*) AS calls,
                   SUM(error IS NOT NULL) AS errors,
                   AVG(latency_ms) AS avg_ms,
                   MAX(latency_ms) AS max_ms,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cost_usd), 0) AS cost_usd
            FROM traces GROUP BY name ORDER BY calls DESC, name
        """
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql)]
