"""Typed bridge, AgentTeams, and evidence-envelope contracts."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


OFFICIAL_MAIN_COMMIT = "223ddc2b8073e4c8b93bcbb15e1d717f196c04d9"
OFFICIAL_STABLE_TAG = "v1.2.2"
OFFICIAL_STABLE_COMMIT = "849182af8e017168a5a200a87b1062142caf462d"
AGENTTEAMS_API_VERSION = "agentteams.io/v1beta1"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpstreamModel(BaseModel):
    """Official response subset; additive upstream fields remain compatible."""

    model_config = ConfigDict(extra="allow")


class RunState(str, Enum):
    PROVISIONING = "PROVISIONING"
    PRE_APPROVAL = "PRE_APPROVAL"
    WAITING_R2 = "WAITING_R2"
    POST_APPROVAL = "POST_APPROVAL"
    COMPENSATION_REQUIRED = "COMPENSATION_REQUIRED"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"


class EnvelopeKind(str, Enum):
    TASK_REQUEST = "TASK_REQUEST"
    TASK_UPDATE = "TASK_UPDATE"
    ARTIFACT_ACCEPTED = "ARTIFACT_ACCEPTED"
    CONFLICT = "CONFLICT"
    REPLAN = "REPLAN"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    COMPENSATION = "COMPENSATION"
    TERMINAL = "TERMINAL"


class CollaborationEnvelope(StrictModel):
    schema_name: Literal["egoagentos.agentteams-envelope.v2"] = Field(
        default="egoagentos.agentteams-envelope.v2", alias="schema"
    )
    envelope_id: str = Field(default_factory=lambda: "env_%s" % uuid.uuid4().hex)
    task_id: str = Field(min_length=3)
    project_id: str = Field(min_length=3)
    trace_id: str = Field(min_length=8)
    correlation_id: str = Field(min_length=8)
    context_version: int = Field(ge=1)
    attempt: int = Field(default=1, ge=1)
    kind: EnvelopeKind
    sender: str = Field(pattern=r"^[a-z0-9._-]+$")
    recipient: str
    causation_id: Optional[str] = None
    body: Dict[str, Any]
    body_sha256: str
    created_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("body_sha256")
    @classmethod
    def digest_format(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("body_sha256 must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def body_digest_matches(self) -> "CollaborationEnvelope":
        if canonical_sha256(self.body) != self.body_sha256:
            raise ValueError("body_sha256 does not match canonical body")
        return self

    @classmethod
    def build(
        cls,
        *,
        task_id: str,
        project_id: str,
        trace_id: str,
        correlation_id: str,
        context_version: int,
        kind: EnvelopeKind,
        sender: str,
        recipient: str,
        body: Dict[str, Any],
        attempt: int = 1,
        causation_id: Optional[str] = None,
    ) -> "CollaborationEnvelope":
        return cls(
            task_id=task_id,
            project_id=project_id,
            trace_id=trace_id,
            correlation_id=correlation_id,
            context_version=context_version,
            kind=kind,
            sender=sender,
            recipient=recipient,
            attempt=attempt,
            causation_id=causation_id,
            body=body,
            body_sha256=canonical_sha256(body),
        )


class ResearchTaskSpec(StrictModel):
    task_id: str
    title: str
    stage: str
    assigned_worker: str
    assigned_to: str
    depends_on: List[str] = Field(default_factory=list)
    expected_skills: List[str] = Field(default_factory=list)
    attempt: int = Field(default=1, ge=1)
    status: str = "planned"
    origin_task_id: Optional[str] = None


class StartRunRequest(StrictModel):
    ego_task_id: str = Field(min_length=3)
    objective: str = Field(min_length=8, max_length=4000)
    team: str = "ego-researchops"
    context_version: int = Field(default=1, ge=1)
    trace_id: str = Field(default_factory=lambda: "trace_%s" % uuid.uuid4().hex)
    correlation_id: str = Field(default_factory=lambda: "corr_%s" % uuid.uuid4().hex)
    ack_timeout_seconds: int = Field(default=300, ge=5, le=86400)
    execution_timeout_seconds: int = Field(default=3600, ge=30, le=604800)
    max_reassignments: int = Field(default=2, ge=0, le=10)
    mode: Literal["live", "dry_run"] = "live"


class GrantRequest(StrictModel):
    approval_token: str = Field(min_length=16, max_length=4096)
    idempotency_key: str = Field(min_length=8, max_length=128)


class BridgeRun(StrictModel):
    id: str
    ego_task_id: str
    agentteams_project_id: str
    team: str
    trace_id: str
    correlation_id: str
    context_version: int
    state: RunState
    mode: Literal["live", "dry_run"]
    objective: str
    task_graph: List[ResearchTaskSpec]
    checkpoint: Dict[str, Any] = Field(default_factory=dict)
    ack_timeout_seconds: int
    execution_timeout_seconds: int
    max_reassignments: int
    version: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WorkerResultEnvelope(StrictModel):
    schema_name: Literal["egoagentos.agentteams-result.v1"] = Field(
        default="egoagentos.agentteams-result.v1", alias="schema"
    )
    task_id: str
    project_id: str
    trace_id: str
    context_version: int = Field(ge=1)
    status: Literal["SUCCESS", "SUCCESS_WITH_NOTES", "REVISION_NEEDED", "BLOCKED"]
    artifact_refs: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    suggested_worker: Optional[str] = None
    review_verdict: Optional[Literal["PASS", "WARN", "FAIL"]] = None
    independent_review: bool = False
    output_sha256: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("output_sha256")
    @classmethod
    def output_digest_format(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("output_sha256 must be a lowercase SHA-256 digest")
        return value


class TeamResponse(UpstreamModel):
    name: str
    teamName: Optional[str] = None
    phase: str
    workerMembers: List[Dict[str, str]]
    leaderName: str
    teamRoomID: str
    leaderDMRoomID: Optional[str] = None
    leaderReady: bool
    readyWorkers: int
    totalWorkers: int


class WorkerResponse(UpstreamModel):
    name: str
    phase: str
    state: Optional[str] = None
    skills: List[str] = Field(default_factory=list)
    matrixUserID: str
    roomID: Optional[str] = None
    team: Optional[str] = None
    role: Optional[str] = None


class WorkflowNode(UpstreamModel):
    id: str
    name: str
    status: str
    assignee: Optional[str] = None


class WorkflowEdge(UpstreamModel):
    source: str
    target: str
    conditional: bool = False


class TaskDetail(UpstreamModel):
    task_id: str
    project_id: str
    status: str
    spec_path: Optional[str] = None
    assigned_to: Optional[str] = None
    summary: Optional[str] = None
    result_status: Optional[str] = None
    deliverables: List[Any] = Field(default_factory=list)
    result_path: Optional[str] = None
    cancel_reason: Optional[str] = None


class WorkflowResponse(UpstreamModel):
    project_id: str
    title: str
    status: str
    plan_type: str
    team_id: Optional[str] = None
    mode: Optional[str] = None
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge] = Field(default_factory=list)
    next: List[str] = Field(default_factory=list)
    interrupts: List[Dict[str, Any]] = Field(default_factory=list)
    tasks_detail: List[TaskDetail] = Field(default_factory=list)


class SpawnRecord(UpstreamModel):
    session_id: str
    name: Optional[str] = None
    status: str
    root_session_id: Optional[str] = None
    spawn: bool = True
    subagent_skills: List[str] = Field(default_factory=list)
    subagent_allowed_tools: List[str] = Field(default_factory=list)


class SpawnWorker(UpstreamModel):
    worker: str
    spawns: List[SpawnRecord]


class ProjectSpawns(UpstreamModel):
    project_id: str
    workers: List[SpawnWorker]


class SpawnMessage(UpstreamModel):
    seq: int
    kind: str
    role: str
    content: Optional[str] = None
    name: Optional[str] = None
    tool_state: Optional[str] = None
    created_at: Optional[str] = None


class SpawnMessages(UpstreamModel):
    session_id: str
    task: str
    messages: List[SpawnMessage]
    has_more: bool = False


class SkillEvidenceLevel(str, Enum):
    DECLARED = "DECLARED"
    SPAWN_AUTHORIZED = "SPAWN_AUTHORIZED"
    TOOL_INVOKED = "TOOL_INVOKED"


class SkillEvidence(StrictModel):
    worker: str
    skill: Optional[str] = None
    tool: Optional[str] = None
    level: SkillEvidenceLevel
    session_id: Optional[str] = None
    message_seq: Optional[int] = None
    source_endpoint: str
    source_sha256: str


class ReconcileResult(StrictModel):
    run: BridgeRun
    workflow_sha256: Optional[str] = None
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    live: bool
