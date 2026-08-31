"""Persistence contract shared by the SQLite and PostgreSQL control-plane stores."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union

from .models import (
    ApprovalRecord,
    AuditEvent,
    EvidenceRecord,
    MemoryCandidate,
    MemoryRecord,
    Stage,
    TaskRecord,
)
from .trusted_memory.models import (
    CandidateFact,
    ConflictRecord,
    LegacyMemoryView,
    LifecycleTransition,
    MemoryState,
    RevocationRecord,
    SupersessionRecord,
    TrustedFact,
)
from benchmarks.secure_memory.canonical import canonical_sha256


TrustedMemoryRecord = Union[
    CandidateFact,
    TrustedFact,
    LifecycleTransition,
    ConflictRecord,
    SupersessionRecord,
    RevocationRecord,
]


def trusted_memory_record_type(record: TrustedMemoryRecord) -> str:
    if isinstance(record, CandidateFact):
        return "candidate"
    if isinstance(record, TrustedFact):
        return "trusted_fact"
    if isinstance(record, LifecycleTransition):
        return "lifecycle"
    if isinstance(record, ConflictRecord):
        return "conflict"
    if isinstance(record, SupersessionRecord):
        return "supersession"
    if isinstance(record, RevocationRecord):
        return "revocation"
    raise TypeError("unsupported trusted-memory record")


def trusted_memory_event_hash(
    *,
    tenant_id: str,
    project_id: str,
    lineage_id: str,
    sequence: int,
    event_type: str,
    record_sha256: str,
    previous_hash: str,
) -> str:
    return canonical_sha256(
        "trusted-memory-history-event",
        {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "lineage_id": lineage_id,
            "sequence": sequence,
            "event_type": event_type,
            "record_sha256": record_sha256,
            "previous_hash": previous_hash,
        },
    )


@dataclass(frozen=True)
class TrustedMemoryEvent:
    tenant_id: str
    project_id: str
    lineage_id: str
    sequence: int
    event_type: str
    record_bytes: bytes
    record_sha256: str
    previous_hash: str
    event_hash: str


@dataclass(frozen=True)
class TrustedMemoryCurrent:
    tenant_id: str
    project_id: str
    lineage_id: str
    revision_id: str
    revision: int
    fact_digest: str
    state: MemoryState
    fact_bytes: bytes
    fact_event_hash: str
    projection_event_hash: str


@dataclass(frozen=True)
class DecisionClosureRecord:
    tenant_id: str
    project_id: str
    closure_digest: str
    closure_bytes: bytes
    closure_bytes_sha256: str
    idempotency_key: str


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

    def list_memory_candidates(self, task_id: str, generation: str) -> List[MemoryCandidate]: ...

    def add_memory(self, record: MemoryRecord) -> None: ...

    def list_memories(self, task_id: str, generation: str) -> List[MemoryRecord]: ...

    def append_trusted_memory_record(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
        record: TrustedMemoryRecord,
        idempotency_key: str,
        expected_current_event_hash: Optional[str] = None,
    ) -> TrustedMemoryEvent: ...

    def append_decision_closure(
        self,
        *,
        tenant_id: str,
        project_id: str,
        closure_digest: str,
        closure_bytes: bytes,
        idempotency_key: str,
    ) -> DecisionClosureRecord: ...

    def get_decision_closure(
        self, *, tenant_id: str, project_id: str, closure_digest: str
    ) -> DecisionClosureRecord: ...

    def get_trusted_memory_event(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
        event_hash: str,
    ) -> TrustedMemoryEvent: ...

    def list_trusted_memory_history(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> List[TrustedMemoryEvent]: ...

    def get_current_trusted_fact(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
    ) -> Optional[TrustedMemoryCurrent]: ...

    def get_trusted_memory_stream_root(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
    ) -> str: ...

    def verify_trusted_memory_stream(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
    ) -> bool: ...

    def list_legacy_memory_views(
        self,
        *,
        task_id: str,
        generation: str,
        tenant_id: str,
        project_id: str,
        version: str,
    ) -> List[LegacyMemoryView]: ...

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
