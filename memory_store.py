import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init(self):
        con = self._connect()
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS debates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                topic TEXT NOT NULL,
                context TEXT,
                verdict TEXT,
                transcript_json TEXT NOT NULL
            )
            """
        )
        con.commit()
        con.close()

    def save(self, topic: str, context: str, verdict: str, transcript: dict) -> int:
        con = self._connect()
        cur = con.execute(
            "INSERT INTO debates(created_at, topic, context, verdict, transcript_json) VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                topic,
                context,
                verdict,
                json.dumps(transcript, ensure_ascii=False),
            ),
        )
        con.commit()
        row_id = cur.lastrowid
        con.close()
        return row_id

    def recall(self, query: str, limit: int = 4) -> List[Tuple]:
        words = [w for w in query.replace("\n", " ").split() if len(w) >= 2][:8]
        if not words:
            return []
        clauses = " OR ".join(["topic LIKE ? OR context LIKE ?" for _ in words])
        params = []
        for word in words:
            params += [f"%{word}%", f"%{word}%"]
        params.append(limit)
        con = self._connect()
        rows = con.execute(
            f"SELECT id, created_at, topic, verdict FROM debates WHERE {clauses} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        con.close()
        return rows
