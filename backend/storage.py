# backend/storage.py
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_SETTINGS: Dict[str, Any] = {
    "model": "gpt-5",
    "reasoning": {"effort": "medium", "summary": "auto"},
    "text": {"verbosity": "high"},
    "tool_allowlist": None  # or list of tool names; None => all available
}


def _now() -> int:
    return int(time.time())


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


class Storage:
    def __init__(self, db_path: str = "./data/app.sqlite3"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = _connect(db_path)
        self._init_schema()

    def _init_schema(self):
        c = self.conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
              id TEXT PRIMARY KEY,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              title TEXT,
              settings TEXT NOT NULL
            );
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY,
              conversation_id TEXT NOT NULL,
              idx INTEGER NOT NULL,
              role TEXT,
              payload TEXT NOT NULL,
              FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            """
        )
        c.close()

    # -------- Conversations --------
    def create_conversation(self, title: Optional[str] = None,
                            settings: Optional[Dict[str, Any]] = None) -> str:
        conv_id = uuid.uuid4().hex
        ts = _now()
        settings_json = json.dumps(settings or DEFAULT_SETTINGS, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO conversations (id, created_at, updated_at, title, settings) VALUES (?,?,?,?,?)",
            (conv_id, ts, ts, title, settings_json),
        )
        return conv_id

    def list_conversations(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, title, created_at, updated_at,
              (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
            FROM conversations c
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?;
            """,
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        conv = self.conn.execute(
            "SELECT id, title, created_at, updated_at, settings FROM conversations WHERE id=?",
            (conv_id,),
        ).fetchone()
        if not conv:
            return None
        msgs = self.conn.execute(
            """
            SELECT id, idx, role, payload
            FROM messages
            WHERE conversation_id=?
            ORDER BY idx ASC;
            """,
            (conv_id,),
        ).fetchall()
        return {
            "id": conv["id"],
            "title": conv["title"],
            "created_at": conv["created_at"],
            "updated_at": conv["updated_at"],
            "settings": json.loads(conv["settings"]),
            "messages": [
                {
                    "id": r["id"],
                    "idx": r["idx"],
                    "role": r["role"],
                    "payload": json.loads(r["payload"]),
                }
                for r in msgs
            ],
        }

    def get_items_for_agent(self, conv_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT payload FROM messages WHERE conversation_id=? ORDER BY idx ASC",
            (conv_id,),
        ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def _next_index(self, conv_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(idx), -1) + 1 AS next_idx FROM messages WHERE conversation_id=?",
            (conv_id,),
        ).fetchone()
        return int(row["next_idx"])

    def append_messages(self, conv_id: str, payloads: List[Dict[str, Any]]) -> None:
        if not payloads:
            return
        idx = self._next_index(conv_id)
        ts = _now()
        cur = self.conn.cursor()
        for p in payloads:
            msg_id = uuid.uuid4().hex
            role = p.get("role") or p.get("type") or "unknown"
            cur.execute(
                "INSERT INTO messages (id, conversation_id, idx, role, payload) VALUES (?,?,?,?,?)",
                (msg_id, conv_id, idx, role, json.dumps(p, ensure_ascii=False)),
            )
            idx += 1
        cur.close()
        self.conn.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?",
            (ts, conv_id),
        )

