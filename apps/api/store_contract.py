"""Persistence contract shared by the SQLite and PostgreSQL control-plane stores."""

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .models import (
    ApprovalRecord,
    AuditEvent,
    EvidenceRecord,
    MemoryCandidate,
    MemoryRecord,
    Stage,
    TaskRecord,
)


class ResearchStore(Protocol):
    """The complete synchronous store surface used by :class:`ResearchOpsService`."""

    engine: str
    audit_guarantee: str
    location: str
    db_path: str

    def transaction(self) -> AbstractContextManager[None]: ...

    def ping(self) -> bool: ...

    def upsert_seed_task(self, task: TaskRecord) -> None: ...

    def create_task(self, task: TaskRecord) -> None: ...

    def save_task(self, task: TaskRecord, expected_version: int) -> None: ...

    def get_task(self, task_id: str) -> TaskRecord: ...

    def list_tasks(self) -> List[TaskRecord]: ...

    def add_approval(self, approval: ApprovalRecord) -> None: ...

    def save_approval(self, approval: ApprovalRecord) -> None: ...

    def get_approval(self, approval_id: str) -> ApprovalRecord: ...

    def latest_approval(self, task_id: str, generation: str) -> Optional[ApprovalRecord]: ...

    def approval_by_token_hash(self, token_hash: str) -> Optional[ApprovalRecord]: ...

    def add_evidence(self, record: EvidenceRecord) -> None: ...

    def list_evidence(self, task_id: str, generation: str) -> List[EvidenceRecord]: ...

    def add_memory_candidate(self, record: MemoryCandidate) -> None: ...

    def list_memory_candidates(
        self, task_id: str, generation: str
    ) -> List[MemoryCandidate]: ...

    def add_memory(self, record: MemoryRecord) -> None: ...

    def list_memories(self, task_id: str, generation: str) -> List[MemoryRecord]: ...

    def append_event(
        self,
        task_id: str,
        generation: str,
        event_type: str,
        actor: str,
        stage: Optional[Stage],
        payload: Dict[str, Any],
        created_at: Optional[datetime] = None,
    ) -> AuditEvent: ...

    def list_events(
        self, task_id: str, generation: str, after_sequence: int = 0, limit: int = 200
    ) -> List[AuditEvent]: ...

    def verify_event_chain(self, task_id: str, generation: str) -> bool: ...

    def recent_events(
        self,
        limit: int = 30,
        task_id: Optional[str] = None,
        generation: Optional[str] = None,
    ) -> List[AuditEvent]: ...

    def get_idempotent(
        self, method: str, path: str, key: str, request_hash: str
    ) -> Optional[Tuple[int, Dict[str, Any]]]: ...

    def put_idempotent(
        self,
        method: str,
        path: str,
        key: str,
        request_hash: str,
        status_code: int,
        response: Dict[str, Any],
    ) -> None: ...

    def counts(self) -> Dict[str, int]: ...
