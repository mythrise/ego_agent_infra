"""Per-agent physical SQLite + Markdown focus memory with phase-bound compaction."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .ladder import canonical_sha256
from .models import StageCommitRequest, TruthClass


ZERO_HASH = "0" * 64


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_agent_dir(agent_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", agent_id).strip(".-")[:64] or "agent"
    return "%s-%s" % (slug, canonical_sha256(agent_id)[:8])


def _items(values: List[str], limit: int = 8) -> List[str]:
    return [re.sub(r"\s+", " ", value).strip()[:500] for value in values if value.strip()][
        :limit
    ]


class PerAgentFocusMemory:
    """Owns one private database and one deterministic Markdown projection per agent."""

    def __init__(self, root: Path, agent_id: str, max_focus_chars: int = 6000) -> None:
        self.agent_id = agent_id
        self.root = root.resolve()
        self.directory = self.root / _safe_agent_dir(agent_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db_path = self.directory / "memory.sqlite3"
        self.md_path = self.directory / "FOCUS.md"
        self.max_focus_chars = max_focus_chars
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS stage_commits (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    stage_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    previous_sha256 TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS l0_conversation (
                    sequence INTEGER NOT NULL,
                    message_index INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    PRIMARY KEY(sequence, message_index),
                    FOREIGN KEY(sequence) REFERENCES stage_commits(sequence)
                );
                CREATE TABLE IF NOT EXISTS focus_snapshots (
                    sequence INTEGER PRIMARY KEY,
                    markdown TEXT NOT NULL,
                    markdown_sha256 TEXT NOT NULL,
                    source_receipt_sha256 TEXT NOT NULL,
                    FOREIGN KEY(sequence) REFERENCES stage_commits(sequence)
                );
                """
            )

    def _prior_facts(self, connection: sqlite3.Connection) -> List[str]:
        rows = connection.execute(
            "SELECT payload_json FROM stage_commits ORDER BY sequence DESC LIMIT 8"
        ).fetchall()
        facts: List[str] = []
        for row in reversed(rows):
            payload = json.loads(row[0])
            for fact in payload.get("validated_facts", []):
                if fact not in facts:
                    facts.append(fact)
        return facts[-12:]

    def _render_focus(
        self, sequence: int, payload: Dict[str, Any], receipt_sha256: str, facts: List[str]
    ) -> str:
        sections = [
            "# Agent Focus",
            "",
            "> Auto-generated compact projection. SQLite is the local source of truth.",
            "",
            "- Agent: `%s`" % self.agent_id,
            "- Task: `%s`" % payload["task_id"],
            "- Session: `%s`" % payload["session_id"],
            "- Last stage: `%s`" % payload["stage_id"],
            "- Commit sequence: `%d`" % sequence,
            "- Receipt: `%s`" % receipt_sha256,
        ]
        for title, key, values in (
            ("Validated facts", "validated_facts", facts),
            ("Decisions", "decisions", payload["decisions"]),
            ("Evidence", "evidence", payload["evidence"]),
            ("Blockers", "blockers", payload["blockers"]),
            ("Next actions", "next_actions", payload["next_actions"]),
        ):
            sections.extend(["", "## %s" % title, ""])
            normalized = _items(values)
            sections.extend(["- %s" % item for item in normalized] or ["- None"])
        markdown = "\n".join(sections).strip() + "\n"
        if len(markdown) > self.max_focus_chars:
            markdown = markdown[: self.max_focus_chars - 18].rstrip() + "\n\n[focus truncated]\n"
        return markdown

    def _atomic_project(self, markdown: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, tmp_name = tempfile.mkstemp(prefix=".FOCUS.", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(markdown)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.md_path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def commit(self, body: StageCommitRequest) -> Dict[str, Any]:
        payload = body.model_dump(mode="json")
        payload["agent_id"] = self.agent_id
        payload["committed_at"] = _now()
        payload_sha256 = canonical_sha256(payload)
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT receipt_sha256 FROM stage_commits ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_sha256 = previous[0] if previous else ZERO_HASH
            receipt_core = {
                "agent_id": self.agent_id,
                "team_id": body.team_id,
                "session_id": body.session_id,
                "task_id": body.task_id,
                "stage_id": body.stage_id,
                "payload_sha256": payload_sha256,
                "previous_sha256": previous_sha256,
            }
            receipt_sha256 = canonical_sha256(receipt_core)
            cursor = connection.execute(
                """
                INSERT INTO stage_commits(
                    team_id,user_id,session_id,task_id,stage_id,created_at,payload_json,
                    payload_sha256,previous_sha256,receipt_sha256
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    body.team_id,
                    body.user_id,
                    body.session_id,
                    body.task_id,
                    body.stage_id,
                    payload["committed_at"],
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    payload_sha256,
                    previous_sha256,
                    receipt_sha256,
                ),
            )
            sequence = int(cursor.lastrowid)
            connection.executemany(
                "INSERT INTO l0_conversation(sequence,message_index,role,content) VALUES(?,?,?,?)",
                [
                    (sequence, index, message.role, message.content)
                    for index, message in enumerate(body.messages)
                ],
            )
            facts = self._prior_facts(connection)
            markdown = self._render_focus(sequence, payload, receipt_sha256, facts)
            markdown_sha256 = canonical_sha256(markdown)
            connection.execute(
                "INSERT INTO focus_snapshots VALUES(?,?,?,?)",
                (sequence, markdown, markdown_sha256, receipt_sha256),
            )
        self._atomic_project(markdown)
        raw_chars = sum(len(message.content) for message in body.messages)
        return {
            "schema_version": "egoagentos-focus-compact-receipt/v1",
            "truth_class": TruthClass.LIVE_LOCAL.value,
            "agent_id": self.agent_id,
            "physical_database": str(self.db_path),
            "markdown_projection": str(self.md_path),
            "sequence": sequence,
            "stage_id": body.stage_id,
            "payload_sha256": payload_sha256,
            "previous_sha256": previous_sha256,
            "receipt_sha256": receipt_sha256,
            "markdown_sha256": markdown_sha256,
            "raw_context_chars": raw_chars,
            "focus_chars": len(markdown),
            "compacted": True,
        }

    def read(self) -> Dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.sequence,s.stage_id,s.created_at,s.payload_sha256,s.previous_sha256,
                       s.receipt_sha256,f.markdown,f.markdown_sha256
                FROM stage_commits s JOIN focus_snapshots f ON f.sequence=s.sequence
                ORDER BY s.sequence DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return {
                "agent_id": self.agent_id,
                "status": "EMPTY",
                "physical_database": str(self.db_path),
                "markdown_projection": str(self.md_path),
            }
        return {
            "agent_id": self.agent_id,
            "status": "READY",
            **dict(row),
            "physical_database": str(self.db_path),
            "markdown_projection": str(self.md_path),
        }


class AgentMemoryRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root

    def for_agent(self, agent_id: str) -> PerAgentFocusMemory:
        return PerAgentFocusMemory(self.root, agent_id)

    def status(self) -> Dict[str, Any]:
        count = 0
        if self.root.exists():
            count = sum(1 for path in self.root.iterdir() if (path / "memory.sqlite3").exists())
        return {
            "provider": "per-agent-sqlite-markdown",
            "truth_class": TruthClass.LIVE_LOCAL.value,
            "status": "ready",
            "root": str(self.root.resolve()),
            "agent_database_count": count,
            "isolation": "one physical SQLite database and one FOCUS.md per agent",
        }
