from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init(self) -> None:
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
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                debate_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                target_role TEXT NOT NULL,
                provider TEXT,
                question_type TEXT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                metadata_json TEXT,
                FOREIGN KEY(debate_id) REFERENCES debates(id)
            )
            """
        )
        con.commit()
        con.close()

    def save(self, topic: str, context: str, verdict: str, transcript: Dict[str, Any]) -> int:
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
        row_id = int(cur.lastrowid)
        con.close()
        return row_id

    def recall(self, query: str, limit: int = 4) -> List[Tuple]:
        words = [word for word in query.replace("\n", " ").split() if len(word) >= 2][:8]
        if not words:
            return []
        clauses = " OR ".join(["topic LIKE ? OR context LIKE ?" for _ in words])
        params: List[Any] = []
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

    def get(self, debate_id: int) -> Optional[Dict[str, Any]]:
        con = self._connect()
        row = con.execute(
            "SELECT id, created_at, topic, context, verdict, transcript_json FROM debates WHERE id = ?",
            (debate_id,),
        ).fetchone()
        con.close()
        if not row:
            return None
        return {
            "id": row[0],
            "created_at": row[1],
            "topic": row[2],
            "context": row[3] or "",
            "verdict": row[4] or "",
            "transcript": json.loads(row[5]),
        }

    def save_followup(
        self,
        debate_id: int,
        target_role: str,
        provider: str,
        question_type: str,
        question: str,
        answer: str,
        metadata: Dict[str, Any],
    ) -> int:
        con = self._connect()
        cur = con.execute(
            """
            INSERT INTO followups(
                debate_id, created_at, target_role, provider, question_type,
                question, answer, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                debate_id,
                datetime.now(timezone.utc).isoformat(),
                target_role,
                provider,
                question_type,
                question,
                answer,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        con.commit()
        row_id = int(cur.lastrowid)
        con.close()
        return row_id

    def get_followups(self, debate_id: int) -> List[Dict[str, Any]]:
        con = self._connect()
        rows = con.execute(
            """
            SELECT id, created_at, target_role, provider, question_type,
                   question, answer, metadata_json
            FROM followups WHERE debate_id = ? ORDER BY id
            """,
            (debate_id,),
        ).fetchall()
        con.close()
        return [
            {
                "id": row[0],
                "created_at": row[1],
                "target_role": row[2],
                "provider": row[3],
                "question_type": row[4],
                "question": row[5],
                "answer": row[6],
                "metadata": json.loads(row[7] or "{}"),
            }
            for row in rows
        ]
