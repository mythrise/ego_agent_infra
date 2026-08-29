"""Durable, fail-closed orchestration across real AgentTeams services."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from .clients import AgentTeamsClient, EgoClient, MatrixClient
from .errors import BridgeError, LiveAgentTeamsUnavailable, UpstreamError
from .models import (
    OFFICIAL_MAIN_COMMIT,
    BridgeRun,
    CollaborationEnvelope,
    EnvelopeKind,
    GrantRequest,
    ReconcileResult,
    ResearchTaskSpec,
    RunState,
    SkillEvidence,
    SkillEvidenceLevel,
    StartRunRequest,
    TaskDetail,
    WorkerResultEnvelope,
    WorkflowResponse,
    canonical_sha256,
    utc_now,
)
from .store import OPERATION_LEASE_KEY, BridgeStoreContract


PRE_APPROVAL_STAGES = {"CONTEXT", "PLAN", "PLAN_REVIEW"}
POST_APPROVAL_STAGES = {
    "EXECUTE",
    "OBSERVE",
    "EVALUATE",
    "VERIFY",
    "MEMORY_SKILL",
}
TERMINAL_NODE_STATUSES = {"completed", "revision", "blocked"}
ACTIVE_NODE_STATUSES = {"delegated", "in-progress"}
SUCCESS_RESULT_STATUSES = {"SUCCESS", "SUCCESS_WITH_NOTES"}
DEFAULT_OPERATION_LEASE_SECONDS = 900

ROLE_PLAN: Tuple[Tuple[str, str, str, Tuple[str, ...]], ...] = (
    ("context", "CONTEXT", "ego-scout", ("research-memory",)),
    ("plan", "PLAN", "ego-architect", ("research-plan", "ablation-analyzer")),
    ("plan-review", "PLAN_REVIEW", "ego-reviewer", ("evidence-gate",)),
    ("execute", "EXECUTE", "ego-runtime", ("safe-experiment-runner", "dataset-manifest")),
    ("observe", "OBSERVE", "ego-runtime", ("safe-experiment-runner",)),
    ("evaluate", "EVALUATE", "ego-evaluator", ("ablation-analyzer",)),
    ("verify", "VERIFY", "ego-reviewer", ("evidence-gate",)),
    ("memory", "MEMORY_SKILL", "ego-memory-curator", ("research-memory",)),
)


def _safe_project_id(task_id: str, context_version: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id).strip("-._") or "task"
    digest = hashlib.sha256((task_id + ":" + str(context_version)).encode("utf-8")).hexdigest()[:8]
    return "ego-%s-v%d-%s" % (slug[:36], context_version, digest)


def _safe_run_id(project_id: str) -> str:
    digest = hashlib.sha256(("bridge-run:" + project_id).encode("utf-8")).hexdigest()
    return "atrun_%s" % digest[:32]


def _iso_now(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(timezone.utc).isoformat()


def _status_to_raw(status: str) -> str:
    return {
        "pending": "planned",
        "delegated": "assigned",
        "in-progress": "in_progress",
        "completed": "completed",
        "revision": "revision",
        "blocked": "blocked",
    }.get(status, status)


class AgentTeamsBridge:
    def __init__(
        self,
        store: BridgeStoreContract,
        agentteams: AgentTeamsClient,
        matrix: MatrixClient,
        ego: EgoClient,
        *,
        clock: Callable[[], datetime] = utc_now,
        operation_lease_seconds: int = DEFAULT_OPERATION_LEASE_SECONDS,
    ) -> None:
        if not 30 <= operation_lease_seconds <= 3600:
            raise ValueError("operation_lease_seconds must be between 30 and 3600")
        self.store = store
        self.agentteams = agentteams
        self.matrix = matrix
        self.ego = ego
        self.clock = clock
        self.operation_lease_seconds = operation_lease_seconds

    def _claim_operation(self, run_id: str, operation: str) -> Tuple[BridgeRun, str]:
        owner_id = "op_%s" % uuid.uuid4().hex
        run = self.store.claim_operation(
            run_id,
            operation,
            owner_id,
            timeout_seconds=self.operation_lease_seconds,
        )
        return run, owner_id

    def _renew_operation(self, run: BridgeRun, lease_owner: str) -> BridgeRun:
        """Atomically prove ownership and extend the lease before an external write."""

        renewed = self.store.renew_operation(
            run.id,
            lease_owner,
            timeout_seconds=self.operation_lease_seconds,
        )
        checkpoint = dict(run.checkpoint)
        checkpoint[OPERATION_LEASE_KEY] = renewed.checkpoint[OPERATION_LEASE_KEY]
        return run.model_copy(update={"checkpoint": checkpoint})

    def probe_live(self, team_name: str) -> Dict[str, Any]:
        if not self.matrix.token:
            raise LiveAgentTeamsUnavailable(
                "AGENTTEAMS_MATRIX_ACCESS_TOKEN is required for live dispatch"
            )
        if not self.agentteams.health():
            raise LiveAgentTeamsUnavailable("AgentTeams /healthz did not return ok")
        version = self.agentteams.version()
        project_api = self.agentteams.probe_project_api()
        matrix_identity = self.matrix.whoami()
        team = self.agentteams.get_team(team_name)
        if team.phase != "Active" or not team.leaderReady:
            raise LiveAgentTeamsUnavailable(
                "AgentTeams Team is not active with a ready Leader",
                details={
                    "team": team_name,
                    "phase": team.phase,
                    "leader_ready": team.leaderReady,
                    "ready_workers": team.readyWorkers,
                    "total_workers": team.totalWorkers,
                },
            )
        if team.readyWorkers < team.totalWorkers:
            raise LiveAgentTeamsUnavailable(
                "AgentTeams Team has non-ready members",
                details={
                    "team": team_name,
                    "ready_workers": team.readyWorkers,
                    "total_workers": team.totalWorkers,
                },
            )
        return {
            "live": True,
            "contract": {
                "repository": "https://github.com/agentscope-ai/AgentTeams",
                "main_commit": OFFICIAL_MAIN_COMMIT,
                "api_version": "agentteams.io/v1beta1",
                "required_endpoints": [
                    "GET /api/v1/projects",
                    "POST /api/v1/projects",
                    "POST /api/v1/projects/{id}/pause",
                    "POST /api/v1/projects/{id}/resume",
                    "POST /api/v1/projects/{id}/replan",
                    "POST /api/v1/projects/{id}/tasks/{taskId}/cancel",
                    "GET /api/v1/projects/{id}/workflow",
                    "GET /api/v1/projects/{id}/spawns",
                ],
            },
            "controller": version,
            "matrix": {
                "user_id": matrix_identity.get("user_id"),
                "device_id": matrix_identity.get("device_id"),
            },
            "project_count": project_api.get("total", len(project_api.get("projects", []))),
            "team": team.model_dump(mode="json"),
        }

    def _load_workers(
        self, team_name: str, worker_names: Iterable[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Read immutable dispatch metadata without mutating worker lifecycle state."""

        workers: Dict[str, Dict[str, Any]] = {}
        for name in sorted(set(worker_names)):
            worker = self.agentteams.get_worker(name)
            if worker.team and worker.team != team_name:
                raise LiveAgentTeamsUnavailable(
                    "AgentTeams Worker belongs to a different Team",
                    details={"worker": name, "expected_team": team_name, "actual": worker.team},
                )
            workers[name] = worker.model_dump(mode="json")
        return workers

    def _ensure_workers_ready(
        self,
        run: BridgeRun,
        worker_names: Iterable[str],
        lease_owner: str,
    ) -> BridgeRun:
        """Fence every lifecycle POST and reject identity drift before dispatch."""

        planned_workers = run.checkpoint.get("workers", {})
        for name in sorted(set(worker_names)):
            run = self._renew_operation(run, lease_owner)
            worker = self.agentteams.ensure_worker_ready(name)
            if worker.phase not in {"Running", "Ready"}:
                raise LiveAgentTeamsUnavailable(
                    "AgentTeams Worker is not running",
                    details={"worker": name, "phase": worker.phase},
                )
            if worker.team and worker.team != run.team:
                raise LiveAgentTeamsUnavailable(
                    "AgentTeams Worker belongs to a different Team",
                    details={
                        "worker": name,
                        "expected_team": run.team,
                        "actual": worker.team,
                    },
                )
            planned = planned_workers.get(name) or {}
            if planned.get("matrixUserID") != worker.matrixUserID:
                raise BridgeError(
                    "worker_identity_drift",
                    "Worker Matrix identity changed after the start intent was reserved",
                    retryable=False,
                    details={"worker": name},
                )
        return run

    def _build_task_graph(
        self,
        project_id: str,
        workers: Dict[str, Dict[str, Any]],
        *,
        stages: Sequence[str],
    ) -> List[ResearchTaskSpec]:
        tasks: List[ResearchTaskSpec] = []
        previous: Optional[str] = None
        for suffix, stage, worker_name, skills in ROLE_PLAN:
            if stage not in stages:
                continue
            worker = workers[worker_name]
            task_id = "%s-%s" % (project_id, suffix)
            task = ResearchTaskSpec(
                task_id=task_id,
                title="%s · %s" % (stage, suffix.replace("-", " ").title()),
                stage=stage,
                assigned_worker=worker_name,
                assigned_to=str(worker["matrixUserID"]),
                depends_on=[previous] if previous else [],
                expected_skills=list(skills),
            )
            tasks.append(task)
            previous = task_id
        return tasks

    @staticmethod
    def _controller_tasks(tasks: Sequence[ResearchTaskSpec]) -> List[Dict[str, Any]]:
        return [
            {
                "taskId": task.task_id,
                "title": task.title,
                "assignedTo": task.assigned_to,
                "dependsOn": task.depends_on,
                "status": task.status,
            }
            for task in tasks
        ]

    def _envelope(
        self,
        run: BridgeRun,
        kind: EnvelopeKind,
        body: Dict[str, Any],
        *,
        attempt: int = 1,
        causation_id: Optional[str] = None,
    ) -> CollaborationEnvelope:
        return CollaborationEnvelope.build(
            task_id=run.ego_task_id,
            project_id=run.agentteams_project_id,
            trace_id=run.trace_id,
            correlation_id=run.correlation_id,
            context_version=run.context_version,
            kind=kind,
            sender="egoagentos-bridge",
            recipient="agentteams-team-leader",
            attempt=attempt,
            causation_id=causation_id,
            body=body,
        )

    def _leader_context(self, run: BridgeRun) -> Tuple[str, str]:
        team = self.agentteams.get_team(run.team)
        leader = self.agentteams.get_worker(team.leaderName)
        return team.teamRoomID, leader.matrixUserID

    def _send(
        self,
        run: BridgeRun,
        envelope: CollaborationEnvelope,
        lease_owner: str,
    ) -> Tuple[str, BridgeRun]:
        room_id, leader_matrix_id = self._leader_context(run)
        run = self._renew_operation(run, lease_owner)
        event_id, receipt = self.matrix.send_envelope_with_receipt(
            room_id=room_id,
            leader_matrix_id=leader_matrix_id,
            envelope=envelope.model_dump(mode="json", by_alias=True),
            transaction_id=envelope.envelope_id,
        )
        self.store.archive_receipt(
            run.id,
            receipt_key="matrix:%s" % envelope.envelope_id,
            source="matrix",
            kind="raw-message",
            payload=receipt,
            lease_owner=lease_owner,
        )
        already_recorded = any(
            item.get("envelope", {}).get("envelope_id") == envelope.envelope_id
            for item in self.store.events(run.id)["items"]
        )
        if not already_recorded:
            self.store.append_event(run.id, envelope, lease_owner=lease_owner)
        return event_id, run

    @staticmethod
    def _task_request_body(
        run: BridgeRun, workflow: WorkflowResponse
    ) -> Dict[str, Any]:
        return {
            "objective": run.objective,
            "controller_workflow_sha256": canonical_sha256(
                workflow.model_dump(mode="json")
            ),
            "task_graph": [task.model_dump(mode="json") for task in run.task_graph],
            "execution_contract": {
                "runtime": "AgentTeams TeamHarness",
                "required_flow": [
                    "projectflow resolve_project",
                    "taskflow delegate_task",
                    "taskflow ack_task",
                    "taskflow submit_task",
                    "taskflow check_task",
                    "projectflow accept_task_result",
                ],
                "result_envelope_suffix": ".ego-envelope.json",
                "result_schema": "egoagentos.agentteams-result.v1",
                "no_chat_approval": True,
                "primary_artifact_contract": {
                    "synthetic": False,
                    "evidence_by_stage": {
                        "CONTEXT": ["dataset_manifest"],
                        "PLAN": ["config"],
                        "EXECUTE": ["code"],
                        "OBSERVE": ["log", "trace"],
                        "EVALUATE": ["metric"],
                        "VERIFY": ["review"],
                    },
                    "EVALUATE_required_json_fields": [
                        "evaluator",
                        "evaluator_sha256",
                        "deterministic=true",
                        "summary_only=false",
                        "raw_samples",
                        "raw_metric_digest",
                        "results",
                        "gpu_receipt",
                        "synthetic=false",
                    ],
                    "VERIFY_required_json_fields": [
                        "reviewer_id",
                        "reviewed_producers",
                        "reviewed_artifact_sha256",
                        "independent=true",
                        "verdict=PASS",
                        "findings",
                        "synthetic=false",
                    ],
                    "review_rule": (
                        "reviewed_artifact_sha256 must equal the exact effective non-review "
                        "artifact byte-digest set; superseded attempts are excluded"
                    ),
                },
            },
        }

    @staticmethod
    def _validate_ego_live_binding(
        request: StartRunRequest, ego_task: Dict[str, Any]
    ) -> None:
        if ego_task.get("synthetic_demo") is not False:
            raise BridgeError(
                "synthetic_task_rejected",
                "Live AgentTeams work requires an explicitly non-synthetic EgoAgentOS task",
                details={"task_id": request.ego_task_id},
            )
        if ego_task.get("scenario") != "external_live":
            raise BridgeError(
                "ego_live_contract_missing",
                "EgoAgentOS task is not an external_live task created by the live API",
                details={"scenario": ego_task.get("scenario")},
            )
        expected_source = {
            "source": "agentteams",
            "team": request.team,
            "trace_id": request.trace_id,
            "correlation_id": request.correlation_id,
            "context_version": request.context_version,
        }
        actual_source = ego_task.get("live_source")
        if (
            not isinstance(actual_source, dict)
            or any(actual_source.get(key) != value for key, value in expected_source.items())
            or actual_source.get(
                "origin_authentication", "UNVERIFIED_OPERATOR_ASSERTION"
            )
            != "UNVERIFIED_OPERATOR_ASSERTION"
        ):
            raise BridgeError(
                "ego_live_binding_conflict",
                "AgentTeams run identity does not match the task's frozen live source binding",
                details={"expected": expected_source, "actual": ego_task.get("live_source")},
            )
        if ego_task.get("objective") != request.objective:
            raise BridgeError(
                "ego_objective_conflict",
                "AgentTeams objective differs from the frozen EgoAgentOS objective",
            )
        if ego_task.get("stage") != "INTAKE":
            raise BridgeError(
                "ego_task_not_at_intake",
                "A new live bridge run must begin from the EgoAgentOS INTAKE stage",
                details={"stage": ego_task.get("stage")},
            )

    def start_run(self, request: StartRunRequest) -> BridgeRun:
        project_id = _safe_project_id(request.ego_task_id, request.context_version)
        if request.mode == "dry_run":
            run_id = "atrun_%s" % uuid.uuid4().hex
            placeholder_workers = {
                worker: {"matrixUserID": "@%s:fixture.invalid" % worker}
                for _, _, worker, _ in ROLE_PLAN
            }
            graph = self._build_task_graph(
                project_id, placeholder_workers, stages=sorted(PRE_APPROVAL_STAGES)
            )
            return self.store.create_run(
                BridgeRun(
                    id=run_id,
                    ego_task_id=request.ego_task_id,
                    agentteams_project_id=project_id,
                    team=request.team,
                    trace_id=request.trace_id,
                    correlation_id=request.correlation_id,
                    context_version=request.context_version,
                    state=RunState.PROVISIONING,
                    mode="dry_run",
                    objective=request.objective,
                    task_graph=graph,
                    checkpoint={
                        "truth": "DRY_RUN_ONLY",
                        "live": False,
                        "reason": "No AgentTeams, Matrix, or EgoAgentOS request was made",
                    },
                    ack_timeout_seconds=request.ack_timeout_seconds,
                    execution_timeout_seconds=request.execution_timeout_seconds,
                    max_reassignments=request.max_reassignments,
                )
            )

        run_id = _safe_run_id(project_id)
        existing: Optional[BridgeRun]
        try:
            existing = self.store.get_run(run_id)
        except BridgeError as error:
            if error.code != "run_not_found":
                raise
            existing = None
        if existing is not None:
            self._assert_start_reservation(existing, request, project_id)
            if existing.state != RunState.PROVISIONING:
                return existing

        live_probe = self.probe_live(request.team)
        ego_task = self.ego.get_task(request.ego_task_id)
        self._validate_ego_live_binding(request, ego_task)
        team = self.agentteams.get_team(request.team)
        required_workers = [worker for _, _, worker, _ in ROLE_PLAN]
        required_workers.append(team.leaderName)
        workers = self._load_workers(request.team, required_workers)
        graph = self._build_task_graph(project_id, workers, stages=sorted(PRE_APPROVAL_STAGES))
        intent_digest = canonical_sha256(
            {
                "ego_task_id": request.ego_task_id,
                "project_id": project_id,
                "objective": request.objective,
                "team": request.team,
                "trace_id": request.trace_id,
                "correlation_id": request.correlation_id,
                "context_version": request.context_version,
                "task_graph": [task.model_dump(mode="json") for task in graph],
                "ack_timeout_seconds": request.ack_timeout_seconds,
                "execution_timeout_seconds": request.execution_timeout_seconds,
                "max_reassignments": request.max_reassignments,
            }
        )
        if existing is None:
            run = BridgeRun(
                id=run_id,
                ego_task_id=request.ego_task_id,
                agentteams_project_id=project_id,
                team=request.team,
                trace_id=request.trace_id,
                correlation_id=request.correlation_id,
                context_version=request.context_version,
                state=RunState.PROVISIONING,
                mode="live",
                objective=request.objective,
                task_graph=graph,
                checkpoint={
                    "truth": "LIVE",
                    "live_probe": live_probe,
                    "workers": workers,
                    "node_status": {},
                    "accepted_contracts": {},
                    "reassignments": {},
                    "ego_grant_committed": False,
                    "intent_digest": intent_digest,
                    "project_create_committed": False,
                    "project_create_confirmation": "NOT_CONFIRMED",
                    "bridge_api_version": "0.3.0",
                    "official_main_commit": OFFICIAL_MAIN_COMMIT,
                },
                ack_timeout_seconds=request.ack_timeout_seconds,
                execution_timeout_seconds=request.execution_timeout_seconds,
                max_reassignments=request.max_reassignments,
            )
            try:
                run = self.store.create_run(run)
            except BridgeError as error:
                if error.code != "run_conflict":
                    raise
                run = self.store.get_run(run_id)
                self._assert_start_reservation(run, request, project_id)
                if run.checkpoint.get("intent_digest") != intent_digest:
                    raise BridgeError(
                        "start_intent_conflict",
                        "Existing bridge reservation has a different immutable start intent",
                        details={"run_id": run.id, "project_id": project_id},
                    ) from error
                if run.state != RunState.PROVISIONING:
                    return run
        else:
            run = existing
            if run.checkpoint.get("intent_digest") != intent_digest:
                raise BridgeError(
                    "start_intent_conflict",
                    "Existing bridge reservation has a different immutable start intent",
                    details={"run_id": run.id, "project_id": project_id},
                )

        run, lease_owner = self._claim_operation(run.id, "live-start")
        try:
            run = self._ensure_workers_ready(run, required_workers, lease_owner)
            run = self._confirm_reserved_project(
                run,
                request,
                source_room_id=team.teamRoomID,
                lease_owner=lease_owner,
            )
            run = self._renew_operation(run, lease_owner)
            workflow = self.agentteams.replan(
                project_id, request.team, self._controller_tasks(run.task_graph)
            )
            stored_envelope = run.checkpoint.get("start_dispatch_envelope")
            if stored_envelope is None:
                envelope = self._envelope(
                    run,
                    EnvelopeKind.TASK_REQUEST,
                    self._task_request_body(run, workflow),
                )
                checkpoint = dict(run.checkpoint)
                checkpoint["start_dispatch_envelope"] = envelope.model_dump(
                    mode="json", by_alias=True
                )
                run = run.model_copy(update={"checkpoint": checkpoint})
                run = self.store.update_run(
                    run,
                    expected_version=run.version,
                    lease_owner=lease_owner,
                )
            else:
                envelope = CollaborationEnvelope.model_validate(stored_envelope)
            matrix_event_id, run = self._send(run, envelope, lease_owner)
            checkpoint = dict(run.checkpoint)
            checkpoint["dispatch_matrix_event_id"] = matrix_event_id
            checkpoint["matrix_root"] = matrix_event_id
            checkpoint["last_workflow_sha256"] = canonical_sha256(
                workflow.model_dump(mode="json")
            )
            run = run.model_copy(update={"state": RunState.PRE_APPROVAL, "checkpoint": checkpoint})
            self.store.update_run(
                run,
                expected_version=run.version,
                lease_owner=lease_owner,
            )
        except Exception as error:
            if not run.checkpoint.get("project_create_committed"):
                raise
            try:
                run = self._renew_operation(run, lease_owner)
                self.agentteams.pause(project_id, request.team, "bridge dispatch failed")
            except Exception:
                pass
            checkpoint = dict(run.checkpoint)
            checkpoint["compensation_reason"] = str(error)
            checkpoint["compensation_retry"] = {
                "operation": "start-dispatch",
                "resume_state": RunState.PRE_APPROVAL.value,
                "token_required": False,
            }
            run = run.model_copy(
                update={"state": RunState.COMPENSATION_REQUIRED, "checkpoint": checkpoint}
            )
            self.store.update_run(
                run,
                expected_version=run.version,
                lease_owner=lease_owner,
            )
            if isinstance(error, BridgeError):
                error.details.setdefault("bridge_run_id", run.id)
                error.details.setdefault("compensation_operation", "start-dispatch")
                raise
            raise BridgeError(
                "bridge_dispatch_failed",
                "AgentTeams project exists but live dispatch did not complete",
                status_code=502,
                retryable=True,
                details={
                    "bridge_run_id": run.id,
                    "compensation_operation": "start-dispatch",
                    "cause": str(error),
                },
            ) from error
        finally:
            self.store.release_operation(run.id, lease_owner)
        return self.store.get_run(run.id)

    @staticmethod
    def _assert_start_reservation(
        run: BridgeRun, request: StartRunRequest, project_id: str
    ) -> None:
        expected = {
            "ego_task_id": request.ego_task_id,
            "agentteams_project_id": project_id,
            "team": request.team,
            "trace_id": request.trace_id,
            "correlation_id": request.correlation_id,
            "context_version": request.context_version,
            "mode": "live",
            "objective": request.objective,
            "ack_timeout_seconds": request.ack_timeout_seconds,
            "execution_timeout_seconds": request.execution_timeout_seconds,
            "max_reassignments": request.max_reassignments,
        }
        actual = {key: getattr(run, key) for key in expected}
        if actual != expected:
            raise BridgeError(
                "start_reservation_conflict",
                "A deterministic bridge reservation already exists for a different request",
                details={"run_id": run.id, "expected": expected, "actual": actual},
            )

    def _confirm_reserved_project(
        self,
        run: BridgeRun,
        request: StartRunRequest,
        *,
        source_room_id: str,
        lease_owner: str,
    ) -> BridgeRun:
        if run.checkpoint.get("project_create_committed"):
            return run
        existing_receipt = next(
            (
                item
                for item in self.store.receipts(run.id)["items"]
                if item["receipt_key"] == "agentteams:project-create"
            ),
            None,
        )
        if existing_receipt is not None:
            receipt_payload = existing_receipt["payload"]
            if not isinstance(receipt_payload, dict):
                raise BridgeError(
                    "project_receipt_identity_conflict",
                    "Persisted project receipt payload is malformed",
                    details={"run_id": run.id, "project_id": run.agentteams_project_id},
                )
            response_identifier = receipt_payload.get("response_identifier")
            response_digest = receipt_payload.get("response_sha256")
            response_body = receipt_payload.get("response_body")
            request_body = receipt_payload.get("request_body")
            create_receipt_matches = (
                existing_receipt["source"] == "agentteams"
                and existing_receipt["kind"] == "official-response"
                and receipt_payload.get("schema") == "egoagentos.upstream-http-receipt/v1"
                and receipt_payload.get("operation") == "create-project"
                and receipt_payload.get("http_status") == 201
                and isinstance(request_body, dict)
                and request_body.get("project_id") == run.agentteams_project_id
                and request_body.get("team_id") == run.team
                and isinstance(response_body, dict)
                and response_body.get("project_id") == run.agentteams_project_id
            )
            recovery_receipt_matches = (
                existing_receipt["source"] == "agentteams"
                and existing_receipt["kind"] == "recovered-project-observation"
                and receipt_payload.get("schema") == "egoagentos.upstream-http-receipt/v1"
                and receipt_payload.get("operation") == "get-workflow"
                and receipt_payload.get("http_status") == 200
                and isinstance(response_body, dict)
                and response_body.get("project_id") == run.agentteams_project_id
                and response_body.get("team_id") == run.team
            )
            if (
                not (create_receipt_matches or recovery_receipt_matches)
                or response_identifier != run.agentteams_project_id
                or not isinstance(response_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", response_digest) is None
            ):
                raise BridgeError(
                    "project_receipt_identity_conflict",
                    "Persisted project receipt does not prove the reserved AgentTeams identity",
                    details={"run_id": run.id, "project_id": run.agentteams_project_id},
                )
            confirmation = "RECOVERED_FROM_PERSISTED_RECEIPT"
        else:
            try:
                run = self._renew_operation(run, lease_owner)
                create_response, create_receipt = self.agentteams.create_project_with_receipt(
                    project_id=run.agentteams_project_id,
                    title="EgoAgentOS · %s" % request.objective[:120],
                    team=request.team,
                    requester="egoagentos:%s" % request.ego_task_id,
                    source_room_id=source_room_id,
                )
                if create_response.get("project_id") != run.agentteams_project_id:
                    raise BridgeError(
                        "project_create_identity_conflict",
                        "AgentTeams create response does not match the reserved project",
                    )
                confirmation = "OFFICIAL_CREATE_RESPONSE"
                response_identifier = create_response.get("project_id")
                response_digest = canonical_sha256(create_response)
            except UpstreamError as error:
                if error.code != "agentteams_conflict":
                    raise
                workflow, create_receipt = self.agentteams.workflow_with_receipt(
                    run.agentteams_project_id, run.team
                )
                if (
                    workflow.project_id != run.agentteams_project_id
                    or workflow.team_id != run.team
                ):
                    raise BridgeError(
                        "project_recovery_identity_conflict",
                        "Existing AgentTeams project does not match the persisted reservation",
                        details={
                            "expected_project": run.agentteams_project_id,
                            "actual_project": workflow.project_id,
                            "expected_team": run.team,
                            "actual_team": workflow.team_id,
                        },
                    ) from error
                confirmation = "RECOVERED_FROM_OFFICIAL_WORKFLOW"
                response_identifier = workflow.project_id
                response_digest = canonical_sha256(workflow.model_dump(mode="json"))
            self.store.archive_receipt(
                run.id,
                receipt_key="agentteams:project-create",
                source="agentteams",
                kind=(
                    "official-response"
                    if confirmation == "OFFICIAL_CREATE_RESPONSE"
                    else "recovered-project-observation"
                ),
                payload=create_receipt,
                lease_owner=lease_owner,
            )
        checkpoint = dict(run.checkpoint)
        checkpoint["project_create_committed"] = True
        checkpoint["project_create_confirmation"] = confirmation
        checkpoint["project_create_response_sha256"] = response_digest
        checkpoint["project_create_identifier"] = response_identifier
        run = run.model_copy(update={"checkpoint": checkpoint})
        return self.store.update_run(
            run,
            expected_version=run.version,
            lease_owner=lease_owner,
        )

    def get_run(self, run_id: str) -> BridgeRun:
        return self.store.get_run(run_id)

    def acceptance_input_index(self, run_id: str) -> Dict[str, Any]:
        """Index locally frozen inputs for a later acceptance-bundle assembler.

        This endpoint does not claim that a bundle, attestation, or live run exists.  It
        only reports the durable bridge material already present for this run.
        """

        run = self.store.get_run(run_id)
        events = self.store.events(run_id)
        receipts = self.store.receipts(run_id)
        receipt_keys = {item["receipt_key"] for item in receipts["items"]}
        matrix_receipts = [
            item["receipt_id"]
            for item in receipts["items"]
            if item["source"] == "matrix" and item["kind"] == "raw-message"
        ]
        reviewer_receipts = [
            item["receipt_id"]
            for item in receipts["items"]
            if item["kind"] == "reviewer-decision"
        ]
        accepted = run.checkpoint.get("accepted_contracts", {})
        metric_artifacts = [
            contract.get("primary_artifact")
            for contract in accepted.values()
            if isinstance(contract, dict) and contract.get("stage") == "EVALUATE"
        ]
        required = {
            "agentteams:project-create",
            "ego:live-finalization",
            "agentteams:project-complete",
        }
        inputs_ready = (
            run.mode == "live"
            and run.state == RunState.COMPLETED
            and events["chain_valid"]
            and receipts["chain_valid"]
            and required.issubset(receipt_keys)
            and bool(matrix_receipts)
            and bool(reviewer_receipts)
            and bool(metric_artifacts)
            and run.checkpoint.get("ego_gate_status") == "pass"
        )
        return {
            "schema": "egoagentos.acceptance-input-index/v1",
            "run_id": run.id,
            "ego_task_id": run.ego_task_id,
            "agentteams_project_id": run.agentteams_project_id,
            "trace_id": run.trace_id,
            "correlation_id": run.correlation_id,
            "context_version": run.context_version,
            "execution_mode": "real-agentteams" if run.mode == "live" else "dry-run",
            "synthetic": run.mode != "live",
            "external_origin_status": "UNVERIFIED",
            "live_claim_allowed": False,
            "inputs_ready_for_assembly": inputs_ready,
            "bundle_assembled": False,
            "integrity": {
                "bridge_event_chain_valid": events["chain_valid"],
                "receipt_chain_valid": receipts["chain_valid"],
                "ego_finalization_receipt_sha256": run.checkpoint.get(
                    "ego_finalization_receipt_sha256"
                ),
            },
            "indexed": {
                "receipt_keys": sorted(receipt_keys),
                "matrix_receipt_ids": matrix_receipts,
                "reviewer_receipt_ids": reviewer_receipts,
                "metric_artifacts": metric_artifacts,
                "accepted_contracts": accepted,
            },
            "exports": {
                "bridge_events": "/api/v1/agentteams/runs/%s/events" % run.id,
                "upstream_receipts": "/api/v1/agentteams/runs/%s/receipts" % run.id,
                "ego_task": "/api/v1/tasks/%s" % run.ego_task_id,
                "ego_events": "/api/v1/tasks/%s/events" % run.ego_task_id,
                "skill_evidence": "/api/v1/agentteams/runs/%s/skill-evidence" % run.id,
            },
            "assembly_boundary": (
                "Inputs are indexed only. A separate collector must fetch both services, "
                "redact secrets, write acceptance-input.json, and build/verify the immutable bundle."
            ),
        }

    @staticmethod
    def _detail_by_id(workflow: WorkflowResponse) -> Dict[str, TaskDetail]:
        return {detail.task_id: detail for detail in workflow.tasks_detail}

    @staticmethod
    def _deliverable_paths(detail: TaskDetail) -> List[str]:
        paths: List[str] = []
        for item in detail.deliverables:
            if isinstance(item, str):
                paths.append(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                paths.append(item["path"])
        if detail.result_path:
            paths.append(detail.result_path)
        return paths

    def _validate_task_contract(
        self, run: BridgeRun, detail: TaskDetail, lease_owner: str
    ) -> Tuple[WorkerResultEnvelope, str, Dict[str, Any]]:
        paths = self._deliverable_paths(detail)
        envelope_paths = [path for path in paths if path.endswith(".ego-envelope.json")]
        if len(envelope_paths) != 1:
            raise BridgeError(
                "result_envelope_missing",
                "Completed AgentTeams task must declare exactly one .ego-envelope.json artifact",
                details={"task_id": detail.task_id, "deliverables": paths},
            )
        raw, envelope_receipt = self.agentteams.task_artifact_with_receipt(
            run.agentteams_project_id, run.team, detail.task_id, envelope_paths[0]
        )
        envelope_receipt_key = "agentteams:artifact:%s:result-envelope" % detail.task_id
        self.store.archive_receipt(
            run.id,
            receipt_key=envelope_receipt_key,
            source="agentteams",
            kind="official-artifact-response",
            payload=envelope_receipt,
            lease_owner=lease_owner,
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
            envelope = WorkerResultEnvelope.model_validate(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise BridgeError(
                "result_envelope_invalid",
                "AgentTeams result envelope is not valid JSON/schema",
                details={"task_id": detail.task_id, "error": str(error)},
            ) from error
        expected = {
            "task_id": detail.task_id,
            "project_id": run.agentteams_project_id,
            "trace_id": run.trace_id,
            "context_version": run.context_version,
        }
        actual = {key: getattr(envelope, key) for key in expected}
        if actual != expected:
            raise BridgeError(
                "context_conflict",
                "Worker result correlation or context version does not match the live run",
                details={"expected": expected, "actual": actual},
            )
        if envelope.status not in SUCCESS_RESULT_STATUSES or envelope.conflicts:
            raise BridgeError(
                "worker_reported_conflict",
                "Worker result requested revision or reported a conflict",
                details={
                    "task_id": detail.task_id,
                    "status": envelope.status,
                    "conflicts": envelope.conflicts,
                    "suggested_worker": envelope.suggested_worker,
                },
            )
        task_spec = next((task for task in run.task_graph if task.task_id == detail.task_id), None)
        if task_spec and task_spec.stage in {"PLAN_REVIEW", "VERIFY"}:
            if not envelope.independent_review or envelope.review_verdict != "PASS":
                raise BridgeError(
                    "independent_review_not_passed",
                    "Reviewer task must carry independent_review=true and review_verdict=PASS",
                    details={
                        "task_id": detail.task_id,
                        "review_verdict": envelope.review_verdict,
                        "independent_review": envelope.independent_review,
                    },
                )
        if not envelope.artifact_refs:
            raise BridgeError(
                "primary_artifact_missing",
                "Result envelope must bind output_sha256 to at least one declared artifact",
                details={"task_id": detail.task_id},
            )
        primary = envelope.artifact_refs[0]
        if primary not in paths:
            raise BridgeError(
                "undeclared_primary_artifact",
                "Result envelope primary artifact is not declared in AgentTeams TaskMeta",
                details={"task_id": detail.task_id, "path": primary},
            )
        primary_bytes, primary_receipt = self.agentteams.task_artifact_with_receipt(
            run.agentteams_project_id, run.team, detail.task_id, primary
        )
        primary_receipt_key = "agentteams:artifact:%s:primary" % detail.task_id
        self.store.archive_receipt(
            run.id,
            receipt_key=primary_receipt_key,
            source="agentteams",
            kind="official-artifact-response",
            payload=primary_receipt,
            lease_owner=lease_owner,
        )
        actual_digest = hashlib.sha256(primary_bytes).hexdigest()
        if actual_digest != envelope.output_sha256:
            raise BridgeError(
                "artifact_digest_mismatch",
                "Declared output_sha256 does not match the AgentTeams artifact bytes",
                details={
                    "task_id": detail.task_id,
                    "expected": envelope.output_sha256,
                    "actual": actual_digest,
                },
            )
        if task_spec and task_spec.stage in {"EVALUATE", "VERIFY"}:
            content = primary_receipt.get("response_body")
            if not isinstance(content, dict):
                raise BridgeError(
                    "typed_scientific_artifact_missing",
                    "%s task primary artifact must be a JSON object" % task_spec.stage,
                    details={"task_id": detail.task_id, "path": primary},
                )
            if content.get("synthetic") is not False:
                raise BridgeError(
                    "synthetic_scientific_artifact_rejected",
                    "Live metric/review artifacts must explicitly declare synthetic=false",
                    details={"task_id": detail.task_id},
                )
            if task_spec.stage == "EVALUATE":
                required_metric = {
                    "evaluator",
                    "evaluator_sha256",
                    "deterministic",
                    "summary_only",
                    "raw_samples",
                    "raw_metric_digest",
                    "results",
                    "gpu_receipt",
                }
                missing_metric = required_metric - set(content)
                if (
                    missing_metric
                    or content.get("deterministic") is not True
                    or content.get("summary_only") is not False
                ):
                    raise BridgeError(
                        "metric_artifact_contract_invalid",
                        "Evaluator artifact lacks deterministic raw-metric fields",
                        details={"task_id": detail.task_id, "missing": sorted(missing_metric)},
                    )
            else:
                required_review = {
                    "reviewer_id",
                    "reviewed_producers",
                    "reviewed_artifact_sha256",
                    "independent",
                    "verdict",
                }
                missing_review = required_review - set(content)
                if (
                    missing_review
                    or content.get("reviewer_id") != task_spec.assigned_worker
                    or content.get("independent") is not True
                    or content.get("verdict") != "PASS"
                ):
                    raise BridgeError(
                        "reviewer_decision_invalid",
                        "VERIFY artifact is not an independent PASS from its assigned reviewer",
                        details={"task_id": detail.task_id, "missing": sorted(missing_review)},
                    )
                self.store.archive_receipt(
                    run.id,
                    receipt_key="reviewer:%s" % detail.task_id,
                    source="agentteams",
                    kind="reviewer-decision",
                    payload={
                        "task_id": detail.task_id,
                        "assigned_worker": task_spec.assigned_worker,
                        "decision": content,
                        "artifact_response_sha256": primary_receipt["response_sha256"],
                        "result_envelope_sha256": hashlib.sha256(raw).hexdigest(),
                    },
                    lease_owner=lease_owner,
                )
        return (
            envelope,
            hashlib.sha256(raw).hexdigest(),
            {
                "path": primary,
                "content_sha256": actual_digest,
                "size_bytes": len(primary_bytes),
                "media_type": (
                    primary_receipt.get("response_headers", {}).get("content-type")
                    or "application/octet-stream"
                ),
                "receipt_key": primary_receipt_key,
                "receipt_response_sha256": primary_receipt["response_sha256"],
            },
        )

    def _observe_statuses(
        self, run: BridgeRun, workflow: WorkflowResponse, lease_owner: str
    ) -> Tuple[BridgeRun, List[Dict[str, Any]]]:
        checkpoint = dict(run.checkpoint)
        statuses = dict(checkpoint.get("node_status", {}))
        actions: List[Dict[str, Any]] = []
        now = _iso_now(self.clock)
        for node in workflow.nodes:
            previous = statuses.get(node.id)
            if not previous or previous.get("status") != node.status:
                statuses[node.id] = {"status": node.status, "since": now}
                actions.append(
                    {
                        "action": "status_observed",
                        "task_id": node.id,
                        "from": previous.get("status") if previous else None,
                        "to": node.status,
                    }
                )
                envelope = self._envelope(
                    run,
                    EnvelopeKind.TASK_UPDATE,
                    {
                        "agentteams_task_id": node.id,
                        "previous_status": previous.get("status") if previous else None,
                        "status": node.status,
                        "assignee": node.assignee,
                        "source": "GET /api/v1/projects/{id}/workflow?includeTasks=true",
                    },
                )
                self.store.append_event(
                    run.id, envelope, lease_owner=lease_owner
                )
        checkpoint["node_status"] = statuses
        checkpoint["last_workflow_sha256"] = canonical_sha256(
            workflow.model_dump(mode="json")
        )
        return run.model_copy(update={"checkpoint": checkpoint}), actions

    def _seconds_in_status(self, run: BridgeRun, task_id: str) -> float:
        record = run.checkpoint.get("node_status", {}).get(task_id, {})
        since = record.get("since")
        if not since:
            return 0.0
        parsed = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
        return (self.clock().astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()

    def _candidate_workers(
        self, run: BridgeRun, task: ResearchTaskSpec
    ) -> List[Tuple[str, Dict[str, Any]]]:
        workers: Dict[str, Dict[str, Any]] = run.checkpoint.get("workers", {})
        candidates: List[Tuple[str, Dict[str, Any]]] = []
        for name, payload in workers.items():
            if name == task.assigned_worker or payload.get("role") == "team_leader":
                continue
            skills = set(payload.get("skills", []))
            expected = set(task.expected_skills)
            if expected and not expected.intersection(skills):
                continue
            candidates.append((name, payload))
        return sorted(candidates, key=lambda item: item[0])

    def _reassign(
        self,
        run: BridgeRun,
        workflow: WorkflowResponse,
        task: ResearchTaskSpec,
        *,
        reason: str,
        suggested_worker: Optional[str] = None,
        lease_owner: str,
    ) -> Tuple[BridgeRun, Dict[str, Any]]:
        checkpoint = dict(run.checkpoint)
        counts = dict(checkpoint.get("reassignments", {}))
        origin = task.origin_task_id or task.task_id
        count = int(counts.get(origin, 0))
        if count >= run.max_reassignments:
            checkpoint["blocked_reason"] = "reassignment budget exhausted for %s" % origin
            blocked = run.model_copy(update={"state": RunState.BLOCKED, "checkpoint": checkpoint})
            return blocked, {
                "action": "blocked",
                "task_id": task.task_id,
                "reason": checkpoint["blocked_reason"],
            }
        other_active = [
            node.id
            for node in workflow.nodes
            if node.id != task.task_id and node.status == "in-progress"
        ]
        downstream_started = [
            candidate.task_id
            for candidate in run.task_graph
            if task.task_id in candidate.depends_on and candidate.status != "planned"
        ]
        if other_active or downstream_started:
            checkpoint["pending_replan"] = {
                "task_id": task.task_id,
                "reason": reason,
                "blocked_by_active": other_active,
                "blocked_by_downstream": downstream_started,
            }
            return run.model_copy(update={"checkpoint": checkpoint}), {
                "action": "replan_deferred",
                "task_id": task.task_id,
                "active": other_active,
                "downstream": downstream_started,
            }
        candidates = self._candidate_workers(run, task)
        if suggested_worker:
            candidates.sort(key=lambda item: item[0] != suggested_worker)
        if not candidates:
            checkpoint["blocked_reason"] = "no alternate AgentTeams Worker is available"
            return run.model_copy(
                update={"state": RunState.BLOCKED, "checkpoint": checkpoint}
            ), {"action": "blocked", "task_id": task.task_id, "reason": checkpoint["blocked_reason"]}
        worker_name, worker = candidates[count % len(candidates)]
        replacement_id = "%s-r%d" % (origin, count + 1)
        current_node = next((node for node in workflow.nodes if node.id == task.task_id), None)
        current_status = current_node.status if current_node else task.status
        if current_status in ACTIVE_NODE_STATUSES:
            run = self._renew_operation(run, lease_owner)
            self.agentteams.cancel_task(
                run.agentteams_project_id,
                run.team,
                task.task_id,
                reason,
                replacement_id,
            )
            current_status = "blocked"
        graph: List[ResearchTaskSpec] = []
        for existing in run.task_graph:
            updates: Dict[str, Any] = {}
            if existing.task_id == task.task_id:
                updates["status"] = _status_to_raw(current_status)
            if task.task_id in existing.depends_on:
                updates["depends_on"] = [
                    replacement_id if dependency == task.task_id else dependency
                    for dependency in existing.depends_on
                ]
            graph.append(existing.model_copy(update=updates))
        replacement = task.model_copy(
            update={
                "task_id": replacement_id,
                "title": "%s · reassignment %d" % (task.title, count + 1),
                "assigned_worker": worker_name,
                "assigned_to": str(worker["matrixUserID"]),
                "attempt": count + 2,
                "status": "planned",
                "origin_task_id": origin,
            }
        )
        graph.append(replacement)
        run = self._renew_operation(run, lease_owner)
        self.agentteams.replan(
            run.agentteams_project_id, run.team, self._controller_tasks(graph)
        )
        checkpoint[OPERATION_LEASE_KEY] = run.checkpoint[OPERATION_LEASE_KEY]
        counts[origin] = count + 1
        checkpoint["reassignments"] = counts
        checkpoint.pop("pending_replan", None)
        updated = run.model_copy(update={"task_graph": graph, "checkpoint": checkpoint})
        conflict = self._envelope(
            updated,
            EnvelopeKind.CONFLICT,
            {
                "agentteams_task_id": task.task_id,
                "reason": reason,
                "replacement_task_id": replacement_id,
                "replacement_worker": worker_name,
            },
            attempt=replacement.attempt,
        )
        self.store.append_event(
            updated.id, conflict, lease_owner=lease_owner
        )
        replan = self._envelope(
            updated,
            EnvelopeKind.REPLAN,
            {
                "replaced": task.task_id,
                "replacement": replacement.model_dump(mode="json"),
                "task_graph_sha256": canonical_sha256(
                    [item.model_dump(mode="json") for item in graph]
                ),
                "controller_operation": "POST /api/v1/projects/{id}/replan",
            },
            attempt=replacement.attempt,
            causation_id=conflict.envelope_id,
        )
        try:
            matrix_event_id, updated = self._send(updated, replan, lease_owner)
        except Exception as error:
            try:
                updated = self._renew_operation(updated, lease_owner)
                self.agentteams.pause(
                    updated.agentteams_project_id,
                    updated.team,
                    "replan notification failed; compensation fence",
                )
            except Exception:
                pass
            checkpoint = dict(updated.checkpoint)
            checkpoint["compensation_reason"] = str(error)
            checkpoint["compensation_retry"] = {
                "operation": "replan-notify",
                "resume_state": run.state.value,
                "token_required": False,
                "replacement_task_id": replacement_id,
                "replacement_worker": worker_name,
                "reason": reason,
            }
            updated = updated.model_copy(
                update={"state": RunState.COMPENSATION_REQUIRED, "checkpoint": checkpoint}
            )
            self.store.append_event(
                updated.id,
                self._envelope(
                    updated,
                    EnvelopeKind.COMPENSATION,
                    {
                        "fence": "AgentTeams project paused",
                        "operation": "replan-notify",
                        "reason": str(error),
                        "replacement_task_id": replacement_id,
                        "retry": "POST /api/v1/agentteams/runs/{run_id}/reconcile",
                    },
                    attempt=replacement.attempt,
                    causation_id=conflict.envelope_id,
                ),
                lease_owner=lease_owner,
            )
            return updated, {
                "action": "compensation_required",
                "operation": "replan-notify",
                "task_id": replacement_id,
                "reason": str(error),
            }
        return updated, {
            "action": "reassigned",
            "from_task": task.task_id,
            "to_task": replacement_id,
            "to_worker": worker_name,
            "matrix_event_id": matrix_event_id,
        }

    def _first_conflict(
        self, run: BridgeRun, workflow: WorkflowResponse, lease_owner: str
    ) -> Optional[Tuple[ResearchTaskSpec, str, Optional[str]]]:
        details = self._detail_by_id(workflow)
        accepted = dict(run.checkpoint.get("accepted_contracts", {}))
        for task in self._effective_tasks(run):
            node = next((item for item in workflow.nodes if item.id == task.task_id), None)
            if node is None:
                continue
            if node.status in {"revision", "blocked"}:
                detail = details.get(task.task_id)
                suggested = None
                if detail and detail.summary:
                    suggested = None
                return task, "AgentTeams reported terminal %s" % node.status, suggested
            if node.status == "delegated" and self._seconds_in_status(run, task.task_id) > run.ack_timeout_seconds:
                return task, "ACK timeout exceeded", None
            if (
                node.status == "in-progress"
                and self._seconds_in_status(run, task.task_id) > run.execution_timeout_seconds
            ):
                return task, "execution timeout exceeded", None
            if node.status != "completed" or task.task_id in accepted:
                continue
            detail = details.get(task.task_id)
            if detail is None:
                return task, "completed node has no scoped AgentTeams TaskMeta", None
            if detail.result_status not in SUCCESS_RESULT_STATUSES:
                return task, "result_status=%s" % detail.result_status, None
            try:
                envelope, artifact_hash, primary_artifact = self._validate_task_contract(
                    run, detail, lease_owner
                )
            except BridgeError as error:
                suggested = None
                if error.code == "worker_reported_conflict":
                    suggested = error.details.get("suggested_worker")
                return task, "%s: %s" % (error.code, error.message), suggested
            accepted[task.task_id] = {
                "result_envelope_sha256": artifact_hash,
                "output_sha256": envelope.output_sha256,
                "review_verdict": envelope.review_verdict,
                "independent_review": envelope.independent_review,
                "assigned_worker": task.assigned_worker,
                "assigned_to": task.assigned_to,
                "stage": task.stage,
                "primary_artifact": primary_artifact,
                "accepted_at": _iso_now(self.clock),
            }
            run.checkpoint["accepted_contracts"] = accepted
            self.store.append_event(
                run.id,
                self._envelope(
                    run,
                    EnvelopeKind.ARTIFACT_ACCEPTED,
                    {
                        "agentteams_task_id": task.task_id,
                        "result_envelope_sha256": artifact_hash,
                        "output_sha256": envelope.output_sha256,
                        "source": "AgentTeams declared artifact endpoint",
                    },
                ),
                lease_owner=lease_owner,
            )
        return None

    @staticmethod
    def _effective_tasks(
        run: BridgeRun, stages: Optional[set[str]] = None
    ) -> List[ResearchTaskSpec]:
        """Return only the newest attempt for each logical task.

        Controller cancellation deliberately leaves the replaced node in the
        DAG as a terminal audit record.  Counting that superseded node would
        make a successful replacement unable to reach the R2 or completion
        gate.  Attempts are grouped by ``origin_task_id`` and resolved by the
        explicit attempt counter, with graph order as a deterministic tie
        breaker.
        """

        latest: Dict[str, Tuple[int, int, ResearchTaskSpec]] = {}
        for index, task in enumerate(run.task_graph):
            if stages is not None and task.stage not in stages:
                continue
            origin = task.origin_task_id or task.task_id
            candidate = (task.attempt, index, task)
            previous = latest.get(origin)
            if previous is None or candidate[:2] > previous[:2]:
                latest[origin] = candidate
        return [item[2] for item in sorted(latest.values(), key=lambda value: value[1])]

    def _all_stage_tasks_completed(
        self, run: BridgeRun, workflow: WorkflowResponse, stages: set[str]
    ) -> bool:
        nodes = {node.id: node.status for node in workflow.nodes}
        accepted = run.checkpoint.get("accepted_contracts", {})
        tasks = self._effective_tasks(run, stages)
        return bool(tasks) and all(
            nodes.get(task.task_id) == "completed" and task.task_id in accepted for task in tasks
        )

    @staticmethod
    def _assert_ego_run_binding(run: BridgeRun, task: Dict[str, Any]) -> None:
        expected = {
            "source": "agentteams",
            "team": run.team,
            "trace_id": run.trace_id,
            "correlation_id": run.correlation_id,
            "context_version": run.context_version,
        }
        actual = task.get("live_source")
        binding_mismatch = (
            not isinstance(actual, dict)
            or any(actual.get(key) != value for key, value in expected.items())
            or actual.get("origin_authentication", "UNVERIFIED_OPERATOR_ASSERTION")
            != "UNVERIFIED_OPERATOR_ASSERTION"
        )
        if task.get("synthetic_demo") is not False or binding_mismatch:
            raise BridgeError(
                "ego_live_binding_conflict",
                "EgoAgentOS task no longer matches the persisted live run identity",
                details={"expected": expected, "actual": task.get("live_source")},
            )

    def _advance_ego_to_approval(
        self, run: BridgeRun, lease_owner: str
    ) -> Tuple[BridgeRun, Dict[str, Any]]:
        task = self.ego.get_task(run.ego_task_id)
        self._assert_ego_run_binding(run, task)
        stages = ["INTAKE", "CONTEXT", "PLAN", "PLAN_REVIEW", "APPROVAL"]
        if task.get("stage") not in stages:
            raise BridgeError(
                "ego_preapproval_stage_conflict",
                "EgoAgentOS task is outside the pre-approval lifecycle",
                details={"stage": task.get("stage")},
            )
        actions: List[Dict[str, Any]] = []
        index = stages.index(str(task["stage"]))
        for target in stages[index + 1 :]:
            run = self._renew_operation(run, lease_owner)
            response, receipt = self.ego.advance_stage_with_receipt(
                run.ego_task_id,
                target,
                "bridge-%s-%s" % (run.id, target.lower()),
            )
            advanced_task = response.get("task") if isinstance(response, dict) else None
            if not isinstance(advanced_task, dict) or advanced_task.get("stage") != target:
                raise BridgeError(
                    "ego_stage_transition_unverified",
                    "EgoAgentOS returned success without the requested live stage",
                    details={"target": target, "observed": advanced_task},
                )
            task = advanced_task
            self._assert_ego_run_binding(run, task)
            self.store.archive_receipt(
                run.id,
                receipt_key="ego:stage:%s" % target.lower(),
                source="egoagentos",
                kind="control-plane-response",
                payload=receipt,
                lease_owner=lease_owner,
            )
            actions.append({"action": "ego_stage_advanced", "target": target})
        pending = task.get("pending_approval") if isinstance(task, dict) else None
        if task.get("stage") != "APPROVAL" or not isinstance(pending, dict):
            raise BridgeError(
                "ego_approval_contract_missing",
                "EgoAgentOS did not expose a typed pending approval after PLAN_REVIEW",
            )
        checkpoint = dict(run.checkpoint)
        checkpoint["ego_generation"] = task.get("generation")
        checkpoint["ego_task_version"] = task.get("version")
        checkpoint["ego_approval_id"] = pending.get("id")
        return run.model_copy(update={"checkpoint": checkpoint}), {
            "action": "ego_waiting_r2",
            "approval_id": pending.get("id"),
            "transitions": actions,
        }

    @staticmethod
    def _control_receipt(payload: Dict[str, Any]) -> Dict[str, Any]:
        required = {
            "source",
            "operation",
            "method",
            "endpoint",
            "http_status",
            "request_sha256",
            "response_sha256",
            "response_identifier",
        }
        missing = required - set(payload)
        if missing:
            raise BridgeError(
                "upstream_receipt_incomplete",
                "Archived upstream receipt is missing control-plane fields",
                details={"missing": sorted(missing)},
            )
        return {key: payload[key] for key in sorted(required)}

    def _build_finalization_evidence(
        self, run: BridgeRun, ego_task: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        receipt_bundle = self.store.receipts(run.id)
        if not receipt_bundle["chain_valid"]:
            raise BridgeError(
                "receipt_chain_invalid",
                "Bridge upstream receipt chain failed verification",
            )
        receipts = {
            item["receipt_key"]: item for item in receipt_bundle["items"]
        }
        accepted: Dict[str, Dict[str, Any]] = run.checkpoint.get("accepted_contracts", {})
        stage_kinds = {
            "CONTEXT": ("dataset_manifest",),
            "PLAN": ("config",),
            "EXECUTE": ("code",),
            "OBSERVE": ("log", "trace"),
            "EVALUATE": ("metric",),
            "VERIFY": ("review",),
        }
        items: Dict[str, Dict[str, Any]] = {}
        raw_artifact_digests: Dict[str, str] = {}
        review_source: Optional[Tuple[ResearchTaskSpec, Dict[str, Any], Dict[str, Any]]] = None
        matrix_messages = [
            item["payload"]
            for item in receipt_bundle["items"]
            if item["source"] == "matrix" and item["kind"] == "raw-message"
        ]
        for task in self._effective_tasks(run):
            kinds = stage_kinds.get(task.stage)
            if not kinds:
                continue
            contract = accepted.get(task.task_id)
            if not isinstance(contract, dict):
                raise BridgeError(
                    "accepted_contract_missing",
                    "Finalization lacks an accepted AgentTeams artifact contract",
                    details={"task_id": task.task_id, "stage": task.stage},
                )
            artifact = contract.get("primary_artifact")
            if not isinstance(artifact, dict):
                raise BridgeError(
                    "accepted_artifact_missing",
                    "Accepted contract does not identify its primary artifact",
                    details={"task_id": task.task_id},
                )
            receipt_item = receipts.get(str(artifact.get("receipt_key")))
            if receipt_item is None:
                raise BridgeError(
                    "artifact_receipt_missing",
                    "Accepted artifact has no persisted official response receipt",
                    details={"task_id": task.task_id},
                )
            raw_receipt = receipt_item["payload"]
            official_receipt = self._control_receipt(raw_receipt)
            artifact_ref = {
                "uri": "agentteams://%s/%s/%s"
                % (run.agentteams_project_id, task.task_id, artifact["path"]),
                "media_type": artifact["media_type"],
                "content_sha256": artifact["content_sha256"],
                "size_bytes": artifact["size_bytes"],
            }
            raw_artifact_digests[task.stage] = artifact["content_sha256"]
            if task.stage == "EVALUATE":
                content = raw_receipt.get("response_body")
                if not isinstance(content, dict):
                    raise BridgeError(
                        "metric_artifact_body_missing",
                        "Archived evaluator response does not contain the raw JSON artifact",
                    )
                gpu_receipt = content.get("gpu_receipt")
                if not isinstance(gpu_receipt, dict):
                    raise BridgeError(
                        "gpu_receipt_missing",
                        "Evaluator artifact did not bind a GPU execution receipt",
                    )
                payload = {
                    "schema": "egoagentos.external-metric-evidence/v1",
                    "stage": "EVALUATE",
                    "artifact": artifact_ref,
                    "receipts": [official_receipt, self._control_receipt(gpu_receipt)],
                    "evaluator": content.get("evaluator"),
                    "evaluator_sha256": content.get("evaluator_sha256"),
                    "deterministic": content.get("deterministic"),
                    "summary_only": content.get("summary_only"),
                    "raw_samples": content.get("raw_samples"),
                    "raw_metric_digest": content.get("raw_metric_digest"),
                    "results": content.get("results"),
                    "attributes": {
                        "agentteams_project_id": run.agentteams_project_id,
                        "agentteams_task_id": task.task_id,
                        "assigned_worker": task.assigned_worker,
                    },
                    "synthetic": False,
                }
            elif task.stage == "VERIFY":
                content = raw_receipt.get("response_body")
                if not isinstance(content, dict):
                    raise BridgeError(
                        "review_artifact_body_missing",
                        "Archived reviewer response does not contain the raw JSON decision",
                    )
                review_source = (task, content, {"artifact": artifact_ref, "receipt": official_receipt})
                continue
            else:
                source_stage = task.stage
                payload = {
                    "schema": "egoagentos.external-artifact-evidence/v1",
                    "stage": source_stage,
                    "artifact": artifact_ref,
                    "receipts": [official_receipt],
                    "attributes": {
                        "agentteams_project_id": run.agentteams_project_id,
                        "agentteams_task_id": task.task_id,
                        "assigned_worker": task.assigned_worker,
                    },
                    "synthetic": False,
                }
            for kind in kinds:
                item_payload = dict(payload)
                if kind == "trace":
                    attributes = payload.get("attributes")
                    if not isinstance(attributes, dict):
                        raise BridgeError(
                            "trace_attributes_missing",
                            "Trace evidence wrapper lacks structured attributes",
                        )
                    item_payload["attributes"] = {
                        **attributes,
                        "matrix_raw_messages": matrix_messages,
                        "bridge_receipt_chain_head": (
                            receipt_bundle["items"][-1]["receipt_hash"]
                            if receipt_bundle["items"]
                            else None
                        ),
                    }
                    item_payload["receipts"] = [
                        official_receipt,
                        *[
                            self._control_receipt(message)
                            for message in matrix_messages
                        ],
                    ]
                items[kind] = {
                    "generation": ego_task["generation"],
                    "kind": kind,
                    "producer_id": task.assigned_worker,
                    "artifact_digest": canonical_sha256(item_payload),
                    "payload": item_payload,
                    "synthetic": False,
                }

        expected_non_review = {"dataset_manifest", "config", "code", "log", "trace", "metric"}
        if set(items) != expected_non_review or review_source is None:
            raise BridgeError(
                "terminal_evidence_incomplete",
                "Accepted AgentTeams tasks did not produce all terminal evidence kinds",
                details={"present": sorted(items), "required": sorted(expected_non_review)},
            )
        review_task, review_content, review_binding = review_source
        expected_artifact_digests = set(raw_artifact_digests.values()) - {
            raw_artifact_digests.get("VERIFY", "")
        }
        if set(review_content.get("reviewed_artifact_sha256", [])) != expected_artifact_digests:
            raise BridgeError(
                "review_artifact_set_mismatch",
                "Reviewer decision did not bind the exact upstream artifact set",
                details={
                    "expected": sorted(expected_artifact_digests),
                    "actual": sorted(review_content.get("reviewed_artifact_sha256", [])),
                },
            )
        review_payload = {
            "schema": "egoagentos.external-review-evidence/v1",
            "stage": "VERIFY",
            "artifact": review_binding["artifact"],
            "receipts": [review_binding["receipt"]],
            "reviewer_id": review_content.get("reviewer_id"),
            "reviewed_producers": review_content.get("reviewed_producers"),
            "independent": review_content.get("independent"),
            "verdict": review_content.get("verdict"),
            "reviewed_evidence_digests": sorted(
                item["artifact_digest"] for item in items.values()
            ),
            "findings": review_content.get("findings", []),
            "attributes": {
                "agentteams_project_id": run.agentteams_project_id,
                "agentteams_task_id": review_task.task_id,
                "raw_reviewer_decision_sha256": canonical_sha256(review_content),
            },
            "synthetic": False,
        }
        items["review"] = {
            "generation": ego_task["generation"],
            "kind": "review",
            "producer_id": review_task.assigned_worker,
            "artifact_digest": canonical_sha256(review_payload),
            "payload": review_payload,
            "synthetic": False,
        }
        return [items[kind] for kind in sorted(items)]

    def _finalize_ego(
        self, run: BridgeRun, lease_owner: str
    ) -> Tuple[BridgeRun, Dict[str, Any]]:
        checkpoint = dict(run.checkpoint)
        if checkpoint.get("ego_finalization_committed"):
            task = self.ego.get_task(run.ego_task_id)
            self._assert_ego_run_binding(run, task)
            if task.get("stage") != "COMPLETED" or task.get("gate_result", {}).get("status") != "pass":
                raise BridgeError(
                    "ego_finalization_checkpoint_conflict",
                    "Persisted finalization receipt no longer matches EgoAgentOS terminal state",
                )
            return run, task
        task = self.ego.get_task(run.ego_task_id)
        self._assert_ego_run_binding(run, task)
        if task.get("stage") != "EXECUTE":
            raise BridgeError(
                "ego_task_not_at_execute",
                "Terminal evidence may be ingested only after the R2 grant reached EXECUTE",
                details={"stage": task.get("stage")},
            )
        body = {
            "generation": task.get("generation"),
            "expected_task_version": task.get("version"),
            "evidence": self._build_finalization_evidence(run, task),
            "terminal_actor": task.get("owner_agent"),
        }
        run = self._renew_operation(run, lease_owner)
        checkpoint = dict(run.checkpoint)
        response, receipt = self.ego.finalize_live(
            run.ego_task_id,
            body,
            "bridge-finalize-%s-v%d" % (run.id, run.context_version),
        )
        terminal = response.get("task") if isinstance(response, dict) else None
        if (
            not isinstance(terminal, dict)
            or terminal.get("synthetic_demo") is not False
            or terminal.get("stage") != "COMPLETED"
            or terminal.get("gate_result", {}).get("status") != "pass"
            or terminal.get("decision") not in {"KEEP", "DROP", "INCONCLUSIVE"}
        ):
            raise BridgeError(
                "ego_finalization_unverified",
                "EgoAgentOS did not return a valid evidence-gated terminal task",
                details={"observed": terminal},
            )
        archived = self.store.archive_receipt(
            run.id,
            receipt_key="ego:live-finalization",
            source="egoagentos",
            kind="terminal-finalization",
            payload=receipt,
            lease_owner=lease_owner,
        )
        checkpoint["ego_finalization_committed"] = True
        checkpoint["ego_finalization_receipt_sha256"] = archived["payload_sha256"]
        checkpoint["ego_decision"] = terminal["decision"]
        checkpoint["ego_gate_status"] = terminal["gate_result"]["status"]
        checkpoint["ego_terminal_version"] = terminal.get("version")
        updated = run.model_copy(update={"checkpoint": checkpoint})
        return (
            self.store.update_run(
                updated,
                expected_version=run.version,
                lease_owner=lease_owner,
            ),
            terminal,
        )

    def _recover_compensation(
        self, run: BridgeRun, workflow: WorkflowResponse, lease_owner: str
    ) -> Tuple[BridgeRun, Dict[str, Any]]:
        retry = run.checkpoint.get("compensation_retry") or {}
        operation = retry.get("operation")
        if operation == "resume-replan-notify":
            return run, {
                "action": "operator_gate",
                "operation": operation,
                "required_action": (
                    "repeat POST /api/v1/agentteams/runs/{run_id}/r2-grant with the "
                    "same idempotency key; the approval token will not be consumed again"
                ),
            }
        if operation not in {
            "start-dispatch",
            "replan-notify",
            "approval-required-notify",
            "terminal-notify",
        }:
            return run, {
                "action": "operator_gate",
                "operation": operation or "unknown",
                "required_action": "inspect the durable compensation checkpoint",
            }

        if operation == "start-dispatch":
            envelope = self._envelope(
                run,
                EnvelopeKind.TASK_REQUEST,
                self._task_request_body(run, workflow),
            )
            matrix_event_id, run = self._send(run, envelope, lease_owner)
            run = self._renew_operation(run, lease_owner)
            self.agentteams.resume(run.agentteams_project_id, run.team)
            next_state = RunState.PRE_APPROVAL
        elif operation == "replan-notify":
            replacement_id = retry.get("replacement_task_id")
            replacement = next(
                (task for task in run.task_graph if task.task_id == replacement_id), None
            )
            envelope = self._envelope(
                run,
                EnvelopeKind.REPLAN,
                {
                    "recovered": True,
                    "reason": retry.get("reason"),
                    "replacement": (
                        replacement.model_dump(mode="json") if replacement else None
                    ),
                    "task_graph_sha256": canonical_sha256(
                        [task.model_dump(mode="json") for task in run.task_graph]
                    ),
                    "controller_operation": "already committed before compensation fence",
                },
                attempt=replacement.attempt if replacement else 1,
            )
            matrix_event_id, run = self._send(run, envelope, lease_owner)
            run = self._renew_operation(run, lease_owner)
            self.agentteams.resume(run.agentteams_project_id, run.team)
            requested_state = str(retry.get("resume_state", RunState.PRE_APPROVAL.value))
            next_state = (
                RunState(requested_state)
                if requested_state
                in {RunState.PRE_APPROVAL.value, RunState.POST_APPROVAL.value}
                else RunState.PRE_APPROVAL
            )
        elif operation == "approval-required-notify":
            envelope = self._envelope(
                run,
                EnvelopeKind.APPROVAL_REQUIRED,
                {
                    "risk_level": "R2",
                    "project_status": workflow.status,
                    "recovered": True,
                    "required_action": "Approve in EgoAgentOS; chat text is not a grant",
                },
            )
            matrix_event_id, run = self._send(run, envelope, lease_owner)
            next_state = RunState.WAITING_R2
        else:
            envelope = self._envelope(
                run,
                EnvelopeKind.TERMINAL,
                {
                    "agentteams_status": workflow.status,
                    "accepted_contracts": run.checkpoint.get("accepted_contracts", {}),
                    "recovered": True,
                    "ego_decision": run.checkpoint.get("ego_decision"),
                    "ego_gate_status": run.checkpoint.get("ego_gate_status"),
                    "ego_finalization_receipt_sha256": run.checkpoint.get(
                        "ego_finalization_receipt_sha256"
                    ),
                    "claim_boundary": "EgoAgentOS decision is bound to typed live evidence.",
                },
            )
            matrix_event_id, run = self._send(run, envelope, lease_owner)
            next_state = RunState.COMPLETED

        checkpoint = dict(run.checkpoint)
        checkpoint.pop("compensation_reason", None)
        checkpoint.pop("compensation_retry", None)
        if operation == "start-dispatch":
            checkpoint["dispatch_matrix_event_id"] = matrix_event_id
            checkpoint["matrix_root"] = matrix_event_id
        run = run.model_copy(update={"state": next_state, "checkpoint": checkpoint})
        return run, {
            "action": "compensation_recovered",
            "operation": operation,
            "matrix_event_id": matrix_event_id,
            "state": next_state.value,
        }

    def reconcile(self, run_id: str) -> ReconcileResult:
        initial = self.store.get_run(run_id)
        if initial.mode != "live":
            raise BridgeError(
                "dry_run_not_reconcilable",
                "A dry-run plan has no live AgentTeams workflow to reconcile",
                status_code=409,
                details={"truth": "DRY_RUN_ONLY"},
            )
        if initial.state in {RunState.BLOCKED, RunState.COMPLETED}:
            return ReconcileResult(run=initial, live=True, actions=[])
        run, lease_owner = self._claim_operation(run_id, "reconcile")
        try:
            result = self._reconcile_claimed(run, lease_owner)
        finally:
            self.store.release_operation(run_id, lease_owner)
        return result.model_copy(update={"run": self.store.get_run(run_id)})

    def _reconcile_claimed(self, run: BridgeRun, lease_owner: str) -> ReconcileResult:
        workflow, workflow_receipt = self.agentteams.workflow_with_receipt(
            run.agentteams_project_id, run.team
        )
        self.store.archive_receipt(
            run.id,
            receipt_key="agentteams:workflow:%s"
            % workflow_receipt["response_sha256"],
            source="agentteams",
            kind="official-workflow-snapshot",
            payload=workflow_receipt,
            lease_owner=lease_owner,
        )
        if workflow.project_id != run.agentteams_project_id or workflow.team_id != run.team:
            raise BridgeError(
                "workflow_identity_conflict",
                "AgentTeams workflow is not bound to the persisted run identity",
                details={
                    "expected_project": run.agentteams_project_id,
                    "actual_project": workflow.project_id,
                    "expected_team": run.team,
                    "actual_team": workflow.team_id,
                },
        )
        if run.state == RunState.COMPENSATION_REQUIRED:
            run, action = self._recover_compensation(
                run, workflow, lease_owner
            )
            run = self.store.update_run(
                run,
                expected_version=run.version,
                lease_owner=lease_owner,
            )
            return ReconcileResult(
                run=run,
                workflow_sha256=canonical_sha256(workflow.model_dump(mode="json")),
                actions=[action],
                live=True,
            )
        run, actions = self._observe_statuses(run, workflow, lease_owner)
        conflict = self._first_conflict(run, workflow, lease_owner)
        if conflict is not None:
            task, reason, suggested = conflict
            run, action = self._reassign(
                run,
                workflow,
                task,
                reason=reason,
                suggested_worker=suggested,
                lease_owner=lease_owner,
            )
            actions.append(action)
        elif run.state == RunState.PRE_APPROVAL and self._all_stage_tasks_completed(
            run, workflow, PRE_APPROVAL_STAGES
        ):
            run, ego_action = self._advance_ego_to_approval(run, lease_owner)
            actions.append(ego_action)
            run = self._renew_operation(run, lease_owner)
            paused = self.agentteams.pause(
                run.agentteams_project_id,
                run.team,
                "EgoAgentOS R2 approval required before execution",
            )
            run = run.model_copy(update={"state": RunState.WAITING_R2})
            envelope = self._envelope(
                run,
                EnvelopeKind.APPROVAL_REQUIRED,
                {
                    "risk_level": "R2",
                    "project_status": paused.status,
                    "required_action": "Approve in EgoAgentOS; chat text is not a grant",
                    "resume_chain": [
                        "consume scoped EgoAgentOS approval token",
                        "POST AgentTeams project resume",
                        "POST AgentTeams project replan with post-approval DAG",
                        "Matrix APPROVAL_GRANTED event",
                    ],
                },
            )
            try:
                event_id, run = self._send(run, envelope, lease_owner)
                actions.append({"action": "r2_paused", "matrix_event_id": event_id})
            except Exception as error:
                run = self._renew_operation(run, lease_owner)
                checkpoint = dict(run.checkpoint)
                checkpoint["compensation_reason"] = str(error)
                checkpoint["compensation_retry"] = {
                    "operation": "approval-required-notify",
                    "resume_state": RunState.WAITING_R2.value,
                    "token_required": True,
                }
                run = run.model_copy(
                    update={
                        "state": RunState.COMPENSATION_REQUIRED,
                        "checkpoint": checkpoint,
                    }
                )
                self.store.append_event(
                    run.id,
                    self._envelope(
                        run,
                        EnvelopeKind.COMPENSATION,
                        {
                            "fence": "AgentTeams project remains paused",
                            "operation": "approval-required-notify",
                            "reason": str(error),
                            "retry": "POST /api/v1/agentteams/runs/{run_id}/reconcile",
                        },
                    ),
                    lease_owner=lease_owner,
                )
                actions.append(
                    {
                        "action": "compensation_required",
                        "operation": "approval-required-notify",
                        "reason": str(error),
                    }
                )
        elif run.state == RunState.POST_APPROVAL and self._all_stage_tasks_completed(
            run, workflow, POST_APPROVAL_STAGES
        ):
            run, terminal_task = self._finalize_ego(run, lease_owner)
            run = self._renew_operation(run, lease_owner)
            completed, complete_receipt = self.agentteams.complete_with_receipt(
                run.agentteams_project_id, run.team
            )
            self.store.archive_receipt(
                run.id,
                receipt_key="agentteams:project-complete",
                source="agentteams",
                kind="official-response",
                payload=complete_receipt,
                lease_owner=lease_owner,
            )
            run = run.model_copy(update={"state": RunState.COMPLETED})
            envelope = self._envelope(
                run,
                EnvelopeKind.TERMINAL,
                {
                    "agentteams_status": completed.status,
                    "accepted_contracts": run.checkpoint.get("accepted_contracts", {}),
                    "ego_stage": terminal_task["stage"],
                    "ego_decision": terminal_task["decision"],
                    "ego_gate_status": terminal_task["gate_result"]["status"],
                    "ego_finalization_receipt_sha256": run.checkpoint[
                        "ego_finalization_receipt_sha256"
                    ],
                    "claim_boundary": "EgoAgentOS decision is bound to typed live evidence.",
                },
            )
            try:
                event_id, run = self._send(run, envelope, lease_owner)
                actions.append({"action": "agentteams_completed", "matrix_event_id": event_id})
            except Exception as error:
                run = self._renew_operation(run, lease_owner)
                checkpoint = dict(run.checkpoint)
                checkpoint["compensation_reason"] = str(error)
                checkpoint["compensation_retry"] = {
                    "operation": "terminal-notify",
                    "resume_state": RunState.COMPLETED.value,
                    "token_required": False,
                }
                run = run.model_copy(
                    update={
                        "state": RunState.COMPENSATION_REQUIRED,
                        "checkpoint": checkpoint,
                    }
                )
                self.store.append_event(
                    run.id,
                    self._envelope(
                        run,
                        EnvelopeKind.COMPENSATION,
                        {
                            "fence": "AgentTeams project is terminal",
                            "operation": "terminal-notify",
                            "reason": str(error),
                            "retry": "POST /api/v1/agentteams/runs/{run_id}/reconcile",
                        },
                    ),
                    lease_owner=lease_owner,
                )
                actions.append(
                    {
                        "action": "compensation_required",
                        "operation": "terminal-notify",
                        "reason": str(error),
                    }
                )
        run = self.store.update_run(
            run,
            expected_version=run.version,
            lease_owner=lease_owner,
        )
        return ReconcileResult(
            run=run,
            workflow_sha256=canonical_sha256(workflow.model_dump(mode="json")),
            actions=actions,
            live=True,
        )

    def _post_approval_graph(self, run: BridgeRun) -> List[ResearchTaskSpec]:
        workers: Dict[str, Dict[str, Any]] = run.checkpoint.get("workers", {})
        existing_post = [
            task for task in run.task_graph if task.stage in POST_APPROVAL_STAGES
        ]
        post = existing_post or self._build_task_graph(
            run.agentteams_project_id, workers, stages=sorted(POST_APPROVAL_STAGES)
        )
        accepted = run.checkpoint.get("accepted_contracts", {})
        pre = [
            task.model_copy(update={"status": "completed"})
            if task.task_id in accepted
            else task
            for task in run.task_graph
            if task.stage in PRE_APPROVAL_STAGES
        ]
        effective_pre = self._effective_tasks(run, PRE_APPROVAL_STAGES)
        stage_order = {stage: index for index, stage in enumerate(
            ("CONTEXT", "PLAN", "PLAN_REVIEW")
        )}
        effective_pre.sort(key=lambda task: (stage_order.get(task.stage, -1), task.attempt))
        if effective_pre and post:
            post[0] = post[0].model_copy(
                update={"depends_on": [effective_pre[-1].task_id]}
            )
        return pre + post

    def grant_r2(self, run_id: str, request: GrantRequest) -> BridgeRun:
        initial = self.store.get_run(run_id)
        if initial.mode != "live":
            raise BridgeError("dry_run_grant_forbidden", "Cannot grant a dry-run plan")
        if initial.state not in {RunState.WAITING_R2, RunState.COMPENSATION_REQUIRED}:
            raise BridgeError(
                "run_not_waiting_for_r2",
                "Bridge run is not at the R2 recovery gate",
                details={"state": initial.state.value},
            )
        run, lease_owner = self._claim_operation(run_id, "r2-grant")
        try:
            self._grant_r2_claimed(run, request, lease_owner)
        finally:
            self.store.release_operation(run_id, lease_owner)
        return self.store.get_run(run_id)

    def _grant_r2_claimed(
        self, run: BridgeRun, request: GrantRequest, lease_owner: str
    ) -> BridgeRun:
        checkpoint = dict(run.checkpoint)
        if run.state == RunState.COMPENSATION_REQUIRED:
            operation = (checkpoint.get("compensation_retry") or {}).get("operation")
            if operation not in {
                "approval-required-notify",
                "resume-replan-notify",
            }:
                raise BridgeError(
                    "compensation_requires_reconcile",
                    "This compensation is not an R2 token recovery",
                    details={
                        "operation": operation,
                        "required_action": (
                            "POST /api/v1/agentteams/runs/{run_id}/reconcile"
                        ),
                    },
                )
        ego_grant_committed = bool(checkpoint.get("ego_grant_committed"))
        if not ego_grant_committed:
            ego_task = self.ego.get_task(run.ego_task_id)
            if ego_task.get("stage") != "APPROVAL" or not ego_task.get("pending_approval"):
                raise BridgeError(
                    "ego_task_not_at_approval",
                    "EgoAgentOS task must expose a pending APPROVAL before R2 recovery",
                    details={"stage": ego_task.get("stage")},
                )
            pending_approval = ego_task.get("pending_approval") or {}
            run = self._renew_operation(run, lease_owner)
            checkpoint = dict(run.checkpoint)
            checkpoint["grant_id"] = pending_approval.get("id")
            checkpoint["grant_approver"] = pending_approval.get("approver")
            response = self.ego.consume_r2_grant(
                run.ego_task_id, request.approval_token, request.idempotency_key
            )
            advanced_task = response.get("task") if isinstance(response, dict) else None
            advanced_stage = (
                advanced_task.get("stage") if isinstance(advanced_task, dict) else None
            )
            checkpoint["ego_grant_committed"] = True
            checkpoint["ego_grant_response_sha256"] = canonical_sha256(response)
            checkpoint["ego_grant_observed_stage"] = advanced_stage
            checkpoint["ego_grant_idempotency_key_sha256"] = hashlib.sha256(
                request.idempotency_key.encode("utf-8")
            ).hexdigest()
            # Persist immediately: the token is consumed, while the token itself is never stored.
            run = run.model_copy(update={"checkpoint": checkpoint})
            run = self.store.update_run(
                run,
                expected_version=run.version,
                lease_owner=lease_owner,
            )
            if advanced_stage != "EXECUTE":
                checkpoint = dict(run.checkpoint)
                checkpoint["compensation_reason"] = (
                    "EgoAgentOS returned success without an EXECUTE task state"
                )
                checkpoint["compensation_retry"] = {
                    "operation": "grant-response-uncertain",
                    "token_required": False,
                    "observed_stage": advanced_stage,
                }
                run = run.model_copy(
                    update={
                        "state": RunState.COMPENSATION_REQUIRED,
                        "checkpoint": checkpoint,
                    }
                )
                self.store.update_run(
                    run,
                    expected_version=run.version,
                    lease_owner=lease_owner,
                )
                raise BridgeError(
                    "ego_grant_transition_unverified",
                    "R2 receipt was persisted but the EgoAgentOS EXECUTE transition was not verified",
                    retryable=False,
                    details={"observed_stage": advanced_stage, "token_reusable": False},
                )
        graph = self._post_approval_graph(run)
        try:
            run = self._renew_operation(run, lease_owner)
            self.agentteams.resume(run.agentteams_project_id, run.team)
            run = self._renew_operation(run, lease_owner)
            self.agentteams.replan(
                run.agentteams_project_id, run.team, self._controller_tasks(graph)
            )
            run = run.model_copy(update={"task_graph": graph, "state": RunState.POST_APPROVAL})
            envelope = self._envelope(
                run,
                EnvelopeKind.APPROVAL_GRANTED,
                {
                    "risk_level": "R2",
                    "ego_grant_committed": True,
                    "approval_token_persisted": False,
                    "post_approval_task_graph_sha256": canonical_sha256(
                        [task.model_dump(mode="json") for task in graph]
                    ),
                    "resume_source": "EgoAgentOS scoped approval token",
                },
            )
            event_id, run = self._send(run, envelope, lease_owner)
            checkpoint = dict(run.checkpoint)
            checkpoint["approval_granted_matrix_event_id"] = event_id
            checkpoint.pop("compensation_reason", None)
            checkpoint.pop("compensation_retry", None)
            run = run.model_copy(update={"checkpoint": checkpoint})
            return self.store.update_run(
                run,
                expected_version=run.version,
                lease_owner=lease_owner,
            )
        except Exception as error:
            try:
                run = self._renew_operation(run, lease_owner)
                self.agentteams.pause(
                    run.agentteams_project_id,
                    run.team,
                    "post-grant recovery failed; compensation fence",
                )
            except Exception:
                pass
            checkpoint = dict(run.checkpoint)
            checkpoint["compensation_reason"] = str(error)
            checkpoint["compensation_retry"] = {
                "operation": "resume-replan-notify",
                "token_required": False,
                "ego_grant_committed": True,
            }
            run = run.model_copy(
                update={"state": RunState.COMPENSATION_REQUIRED, "checkpoint": checkpoint}
            )
            run = self.store.update_run(
                run,
                expected_version=run.version,
                lease_owner=lease_owner,
            )
            self.store.append_event(
                run.id,
                self._envelope(
                    run,
                    EnvelopeKind.COMPENSATION,
                    {
                        "fence": "AgentTeams project paused",
                        "reason": str(error),
                        "retry": "repeat r2-grant with the same idempotency key; token is not reused",
                    },
                ),
                lease_owner=lease_owner,
            )
            raise

    def skill_evidence(self, run_id: str) -> Dict[str, Any]:
        run = self.store.get_run(run_id)
        if run.mode != "live":
            raise BridgeError(
                "dry_run_has_no_skill_trace",
                "Dry-run fixtures do not prove AgentTeams Skill discovery or invocation",
            )
        evidence: List[SkillEvidence] = []
        workers: Dict[str, Dict[str, Any]] = run.checkpoint.get("workers", {})
        for worker_name, worker in sorted(workers.items()):
            endpoint = "/api/v1/workers/%s" % worker_name
            digest = canonical_sha256(worker)
            for skill in worker.get("skills", []):
                evidence.append(
                    SkillEvidence(
                        worker=worker_name,
                        skill=str(skill),
                        level=SkillEvidenceLevel.DECLARED,
                        source_endpoint=endpoint,
                        source_sha256=digest,
                    )
                )
        spawns = self.agentteams.spawns(run.agentteams_project_id, run.team)
        spawns_payload = spawns.model_dump(mode="json")
        spawns_digest = canonical_sha256(spawns_payload)
        for worker_group in spawns.workers:
            for spawn in worker_group.spawns:
                for skill in spawn.subagent_skills:
                    evidence.append(
                        SkillEvidence(
                            worker=worker_group.worker,
                            skill=skill,
                            level=SkillEvidenceLevel.SPAWN_AUTHORIZED,
                            session_id=spawn.session_id,
                            source_endpoint="/api/v1/projects/{id}/spawns",
                            source_sha256=spawns_digest,
                        )
                    )
                messages = self.agentteams.spawn_messages(
                    run.agentteams_project_id, run.team, spawn.session_id
                )
                message_payload = messages.model_dump(mode="json")
                message_digest = canonical_sha256(message_payload)
                for message in messages.messages:
                    if message.kind == "tool_result" and message.tool_state == "success":
                        evidence.append(
                            SkillEvidence(
                                worker=worker_group.worker,
                                tool=message.name or "unknown",
                                level=SkillEvidenceLevel.TOOL_INVOKED,
                                session_id=spawn.session_id,
                                message_seq=message.seq,
                                source_endpoint=(
                                    "/api/v1/projects/{id}/spawns/{sessionId}/messages"
                                ),
                                source_sha256=message_digest,
                            )
                        )
        return {
            "live": True,
            "project_id": run.agentteams_project_id,
            "items": [item.model_dump(mode="json") for item in evidence],
            "claim_boundary": {
                "DECLARED": "Worker CR spec.skills contains the Skill assignment",
                "SPAWN_AUTHORIZED": "official spawn trace contains subagent_skills",
                "TOOL_INVOKED": "official spawn message stream contains a successful tool_result",
                "not_claimed": (
                    "A Skill assignment alone is not claimed as execution; a tool result is not "
                    "claimed as a specific Skill unless independently linked by task artifacts/trace."
                ),
            },
        }

    def recover_active(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for run in self.store.active_runs():
            compensation_operation = (
                (run.checkpoint.get("compensation_retry") or {}).get("operation")
                if run.state == RunState.COMPENSATION_REQUIRED
                else None
            )
            if (
                run.mode != "live"
                or run.state == RunState.WAITING_R2
                or compensation_operation == "resume-replan-notify"
            ):
                results.append(
                    {
                        "run_id": run.id,
                        "state": run.state.value,
                        "action": "operator_gate",
                        "operation": compensation_operation,
                    }
                )
                continue
            try:
                result = self.reconcile(run.id)
                results.append(
                    {
                        "run_id": run.id,
                        "state": result.run.state.value,
                        "actions": result.actions,
                    }
                )
            except BridgeError as error:
                results.append(
                    {"run_id": run.id, "state": run.state.value, "error": error.as_dict()}
                )
        return results
