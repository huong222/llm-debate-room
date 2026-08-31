from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _columns(con: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}

    def _init_schema(self) -> None:
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
        existing = self._columns(con, "debates")
        additions = {
            "version": "TEXT",
            "target": "TEXT",
            "constraints_text": "TEXT",
            "goal": "TEXT",
            "research": "TEXT",
        }
        for name, sql_type in additions.items():
            if name not in existing:
                con.execute(f"ALTER TABLE debates ADD COLUMN {name} {sql_type}")

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                debate_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                target_role TEXT NOT NULL,
                question_type TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                engine TEXT,
                model TEXT,
                FOREIGN KEY(debate_id) REFERENCES debates(id)
            )
            """
        )
        con.commit()
        con.close()

    def save_debate(
        self,
        *,
        version: str,
        topic: str,
        target: str,
        constraints_text: str,
        goal: str,
        context: str,
        research: str,
        verdict: str,
        transcript: Dict[str, Any],
    ) -> int:
        con = self._connect()
        cur = con.execute(
            """
            INSERT INTO debates(
                created_at, version, topic, target, constraints_text,
                goal, context, research, verdict, transcript_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                version,
                topic,
                target,
                constraints_text,
                goal,
                context,
                research,
                verdict,
                json.dumps(transcript, ensure_ascii=False),
            ),
        )
        con.commit()
        debate_id = int(cur.lastrowid)
        con.close()
        return debate_id

    def add_followup(
        self,
        *,
        debate_id: int,
        target_role: str,
        question_type: str,
        question: str,
        answer: str,
        engine: str,
        model: str,
    ) -> int:
        con = self._connect()
        cur = con.execute(
            """
            INSERT INTO followups(
                debate_id, created_at, target_role, question_type,
                question, answer, engine, model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                debate_id,
                datetime.now(timezone.utc).isoformat(),
                target_role,
                question_type,
                question,
                answer,
                engine,
                model,
            ),
        )
        con.commit()
        followup_id = int(cur.lastrowid)
        con.close()
        return followup_id

    def list_followups(self, debate_id: int) -> List[Dict[str, Any]]:
        con = self._connect()
        rows = con.execute(
            "SELECT * FROM followups WHERE debate_id=? ORDER BY id ASC",
            (debate_id,),
        ).fetchall()
        con.close()
        return [dict(row) for row in rows]

    def get_debate(self, debate_id: int) -> Optional[Dict[str, Any]]:
        con = self._connect()
        row = con.execute("SELECT * FROM debates WHERE id=?", (debate_id,)).fetchone()
        con.close()
        if row is None:
            return None
        data = dict(row)
        try:
            transcript = json.loads(data.get("transcript_json") or "{}")
        except json.JSONDecodeError:
            transcript = {}
        transcript.setdefault("debate_id", debate_id)
        transcript.setdefault("version", data.get("version") or "legacy")
        transcript.setdefault("topic", data.get("topic") or "")
        transcript.setdefault("target", data.get("target") or "")
        transcript.setdefault("constraints", data.get("constraints_text") or "")
        transcript.setdefault("goal", data.get("goal") or "")
        transcript.setdefault("context", data.get("context") or "")
        transcript.setdefault("research", data.get("research") or "")
        transcript.setdefault("verdict", data.get("verdict") or "")
        transcript["followups"] = self.list_followups(debate_id)
        return transcript

    def recall(self, query: str, limit: int = 10) -> List[Tuple[int, str, str, str]]:
        words = [word for word in query.replace("\n", " ").split() if len(word) >= 2][:8]
        if not words:
            return []
        clauses: List[str] = []
        params: List[Any] = []
        for word in words:
            like = f"%{word}%"
            clauses.append(
                "(topic LIKE ? OR context LIKE ? OR target LIKE ? OR constraints_text LIKE ? OR goal LIKE ?)"
            )
            params.extend([like, like, like, like, like])
        params.append(limit)
        con = self._connect()
        rows = con.execute(
            f"""
            SELECT id, created_at, topic, verdict
            FROM debates
            WHERE {' OR '.join(clauses)}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        con.close()
        return [(int(row[0]), str(row[1]), str(row[2]), str(row[3] or "")) for row in rows]
