"""Strict live-only benchmark adapter for the AgentTeams research path.

The benchmark runner calls ``run_scenario(scenario, seed, workspace)``.  A
missing live deployment returns SKIP.  Only a fully completed live run returns
PASS and writes a content-addressed trace file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from apps.agentteams_bridge.errors import BridgeError
from apps.agentteams_bridge.main import build_service
from apps.agentteams_bridge.models import (
    OFFICIAL_MAIN_COMMIT,
    BridgeRun,
    GrantRequest,
    RunState,
    StartRunRequest,
    canonical_sha256,
)
from benchmarks.trace_verifier import COMMON_EVENT_TYPES, SCENARIO_REQUIRED_EVENTS


def _value(scenario: Any, name: str, default: Any = None) -> Any:
    if isinstance(scenario, Mapping):
        return scenario.get(name, default)
    return getattr(scenario, name, default)


TRACE_SCHEMA_VERSION = "egoagentos.agentteams-trace/v1"
BENCHMARK_ADAPTER_VERSION = "rxp-bench/v1"
REQUIRED_TRACE_EVENTS = set(COMMON_EVENT_TYPES)
CANONICAL_SCENARIO_EVENTS = {
    scenario_id: set(event_types)
    for scenario_id, event_types in SCENARIO_REQUIRED_EVENTS.items()
}
BRIDGE_LEDGER_HASH_ALGORITHM = "sha256-canonical-json-v1"
_SECRET_FIELD_NAMES = {
    "access_token",
    "api_key",
    "approval_token",
    "auth_token",
    "authorization",
    "client_secret",
    "password",
    "private_key",
    "refresh_token",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)^bearer\s+[A-Za-z0-9._~+/=-]{12,}$"),
    re.compile(r"^sk-[A-Za-z0-9_-]{20,}$"),
    re.compile(r"^(?:ghp|github_pat)_[A-Za-z0-9_]{20,}$"),
)


def _assert_secret_free(value: Any, path: str = "bridge_event_chain") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _SECRET_FIELD_NAMES:
                raise BridgeError(
                    "bridge_ledger_contains_secret",
                    "durable bridge ledger cannot be exported with secret fields",
                    details={"path": "%s.%s" % (path, key)},
                )
            _assert_secret_free(item, "%s.%s" % (path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_secret_free(item, "%s[%d]" % (path, index))
    elif isinstance(value, str) and any(
        pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS
    ):
        raise BridgeError(
            "bridge_ledger_contains_secret",
            "durable bridge ledger cannot be exported with secret material",
            details={"path": path},
        )


def _export_bridge_event_chain(ledger: Mapping[str, Any], run_id: str) -> Dict[str, Any]:
    """Export every secret-free field needed to recompute the durable event chain."""

    if ledger.get("chain_valid") is not True:
        raise BridgeError("bridge_event_chain_invalid", "bridge event hash chain is invalid")
    source_items = ledger.get("items")
    if not isinstance(source_items, list) or not source_items:
        raise BridgeError("bridge_event_chain_empty", "bridge event hash chain is empty")
    items: List[Dict[str, Any]] = []
    for sequence, source in enumerate(source_items, start=1):
        if not isinstance(source, dict):
            raise BridgeError(
                "bridge_event_chain_invalid", "bridge event ledger item is not an object"
            )
        envelope = source.get("envelope")
        _assert_secret_free(envelope, "bridge_event_chain.items[%d].envelope" % (sequence - 1))
        items.append(
            {
                "sequence": sequence,
                "event_id": source.get("event_id"),
                "run_id": run_id,
                "kind": source.get("kind"),
                "envelope": envelope,
                "previous_hash": source.get("previous_hash"),
                "event_hash": source.get("event_hash"),
                "created_at": source.get("created_at"),
            }
        )
    total = ledger.get("total")
    if total != len(items):
        raise BridgeError(
            "bridge_event_chain_invalid", "bridge event ledger total does not match its items"
        )
    return {
        "valid": True,
        "hash_algorithm": BRIDGE_LEDGER_HASH_ALGORITHM,
        "external_origin_status": "UNVERIFIED",
        "total": total,
        "head": items[-1]["event_hash"],
        "source_ledger_total": total,
        "items": items,
    }


def _live_binding(scenario: Any, scenario_id: str) -> Dict[str, Any]:
    binding = {
        "ego_task_id": _value(scenario, "ego_task_id"),
        "objective": _value(scenario, "objective"),
        "approval_token": _value(scenario, "approval_token"),
    }
    path = os.getenv("AGENTTEAMS_BENCHMARK_BINDINGS_FILE")
    if path:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            configured = payload.get(scenario_id, {}) if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            configured = {}
        if isinstance(configured, dict):
            for key in binding:
                if not binding[key] and configured.get(key):
                    binding[key] = configured[key]
    return binding


def _bind_scenario_proof(trace: Dict[str, Any], scenario_id: str) -> bool:
    observed = [str(event.get("type")) for event in trace.get("events", [])]
    required = REQUIRED_TRACE_EVENTS | CANONICAL_SCENARIO_EVENTS.get(scenario_id, set())
    missing = sorted(required - set(observed))
    raw_replay = trace.get("replay")
    replay: Dict[str, Any] = raw_replay if isinstance(raw_replay, dict) else {}
    raw_run_ids = replay.get("run_ids")
    replay_run_ids: List[Any] = raw_run_ids if isinstance(raw_run_ids, list) else []
    raw_digests = replay.get("semantic_digests")
    replay_digests: List[Any] = raw_digests if isinstance(raw_digests, list) else []
    replay_ok = (
        len(replay_run_ids) >= 2
        and len(replay_run_ids) == len(replay_digests)
        and len(set(replay_run_ids)) == len(replay_run_ids)
        and len(set(replay_digests)) == 1
    )
    if not replay_ok:
        missing.append("replay.run_ids+semantic_digests")
    verified = not missing
    trace["scenario_proof"] = {
        "scenario_id": scenario_id,
        "required_event_types": sorted(required),
        "observed_event_types": observed,
        "missing_event_types": missing,
        "replay_verified": replay_ok,
        "replay_run_ids": replay_run_ids,
        "replay_semantic_digests": replay_digests,
        "verified": verified,
        "claim_boundary": (
            "Only events derived from the live bridge, official AgentTeams responses, "
            "and EgoAgentOS responses count; scenario descriptions are not evidence."
        ),
    }
    return verified


def _write_trace(workspace: Path, payload: Dict[str, Any]) -> Tuple[str, str]:
    workspace.mkdir(parents=True, exist_ok=True)
    trace_path = workspace / "agentteams-live-trace.json"
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    temporary = trace_path.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(trace_path)
    return trace_path.name, hashlib.sha256(encoded).hexdigest()


def _trace_event(
    events: List[Dict[str, Any]],
    *,
    event_type: str,
    actor: str,
    run: BridgeRun,
    payload: Dict[str, Any],
) -> None:
    events.append(
        {
            "sequence": len(events) + 1,
            "type": event_type,
            "actor": actor,
            "task_id": run.ego_task_id,
            "correlation_id": run.correlation_id,
            "payload": payload,
        }
    )


def _build_verified_trace(
    service: Any,
    run: BridgeRun,
    *,
    seed: int,
    scenario_id: str,
    probe: Dict[str, Any],
    snapshots: List[Dict[str, Any]],
) -> Dict[str, Any]:
    ledger = service.store.events(run.id)
    if not ledger.get("chain_valid"):
        raise BridgeError("bridge_event_chain_invalid", "bridge event hash chain is invalid")
    bridge_event_chain = _export_bridge_event_chain(ledger, run.id)
    skill_payload = service.skill_evidence(run.id)
    final_ego_task = service.ego.get_task(run.ego_task_id)
    gate = final_ego_task.get("gate_result") or {}
    if not final_ego_task.get("decision"):
        raise BridgeError(
            "ego_decision_missing",
            "AgentTeams completion is not a scientific decision; EgoAgentOS decision is missing",
        )
    if str(gate.get("status", "")).lower() != "pass" or not gate.get(
        "independent_reviewer"
    ):
        raise BridgeError(
            "ego_independent_gate_missing",
            "PASS requires an EgoAgentOS evidence gate with an independent reviewer",
            details={"gate_result": gate},
        )

    checkpoint = run.checkpoint
    rxp = {
        "intent_digest": checkpoint.get("intent_digest"),
        "grant_id": checkpoint.get("grant_id"),
        "receipt_digest": checkpoint.get("ego_grant_response_sha256"),
        "evidence_digest": canonical_sha256(checkpoint.get("accepted_contracts", {})),
        "matrix_root": checkpoint.get("matrix_root"),
    }
    missing_rxp = sorted(key for key, value in rxp.items() if not value)
    if missing_rxp:
        raise BridgeError(
            "rxp_trace_incomplete",
            "live trace is missing required RXP correlation fields",
            details={"missing": missing_rxp},
        )

    agents_by_id: Dict[str, Dict[str, Any]] = {}
    worker_records = checkpoint.get("workers", {})
    task_roles: Dict[str, str] = {}
    for task in run.task_graph:
        task_roles.setdefault(task.assigned_worker, task.stage.lower())
    for worker_id, worker in worker_records.items():
        agents_by_id.setdefault(
            worker_id,
            {
                "id": worker_id,
                "role": task_roles.get(worker_id)
                or str(worker.get("role") or "worker"),
                "matrix_user_id": worker.get("matrixUserID"),
                "source": "GET /api/v1/workers/{name}",
            },
        )
    agents = sorted(agents_by_id.values(), key=lambda item: item["id"])
    if len(agents) < 3:
        raise BridgeError(
            "insufficient_agent_roles",
            "verified trace has fewer than three distinct AgentTeams agents",
            details={"agents": agents},
        )
    leader_id = next(
        (agent["id"] for agent in agents if agent["role"] == "team_leader"),
        None,
    )
    if not leader_id:
        raise BridgeError(
            "team_leader_trace_missing",
            "verified trace has no declared AgentTeams Team Leader",
        )
    grant_approver = str(checkpoint.get("grant_approver") or "human-approver")
    ego_actor = str(
        final_ego_task.get("current_agent")
        or final_ego_task.get("owner_agent")
        or "egoagentos"
    )
    human_id = "human:%s" % grant_approver
    ego_id = "ego:%s" % ego_actor
    principals = [
        {
            "id": "egoagentos-bridge",
            "kind": "bridge",
            "source": "EgoAgentOS AgentTeams bridge",
        },
        {
            "id": human_id,
            "kind": "human",
            "source": "EgoAgentOS scoped R2 grant approver=%s" % grant_approver,
        },
        {
            "id": ego_id,
            "kind": "ego",
            "source": "EgoAgentOS final task actor=%s" % ego_actor,
        },
    ]

    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for item in ledger["items"]:
        by_kind.setdefault(item["kind"], []).append(item)
    if not by_kind.get("TASK_REQUEST"):
        raise BridgeError("task_create_evidence_missing", "TASK_REQUEST event is missing")
    if not by_kind.get("APPROVAL_GRANTED"):
        raise BridgeError("approval_evidence_missing", "APPROVAL_GRANTED event is missing")
    if not by_kind.get("TERMINAL"):
        raise BridgeError("terminal_evidence_missing", "TERMINAL event is missing")

    events: List[Dict[str, Any]] = []
    created = by_kind["TASK_REQUEST"][0]
    _trace_event(
        events,
        event_type="task.created",
        actor="egoagentos-bridge",
        run=run,
        payload={
            "project_id": run.agentteams_project_id,
            "official_project_id": checkpoint.get("project_create_identifier"),
            "official_response_sha256": checkpoint.get("project_create_response_sha256"),
            "bridge_event_id": created["event_id"],
            "bridge_event_hash": created["event_hash"],
            "intent_digest": rxp["intent_digest"],
            "matrix_root": rxp["matrix_root"],
        },
    )
    delegated = [
        item
        for item in by_kind.get("TASK_UPDATE", [])
        if item["envelope"]["body"].get("status") == "delegated"
    ]
    if not delegated:
        raise BridgeError(
            "delegation_trace_missing",
            "No official workflow poll observed an AgentTeams delegated task state",
        )
    for item in delegated:
        _trace_event(
            events,
            event_type="task.delegated",
            actor=leader_id,
            run=run,
            payload={
                "agentteams_task_id": item["envelope"]["body"].get(
                    "agentteams_task_id"
                ),
                "assignee": item["envelope"]["body"].get("assignee"),
                "source_endpoint": item["envelope"]["body"].get("source"),
                "bridge_event_id": item["event_id"],
                "bridge_event_hash": item["event_hash"],
                "matrix_root": rxp["matrix_root"],
            },
        )

    accepted_events = by_kind.get("ARTIFACT_ACCEPTED", [])
    if not accepted_events:
        raise BridgeError("acceptance_trace_missing", "No content-addressed task was accepted")
    for item in accepted_events:
        _trace_event(
            events,
            event_type="task.accepted",
            actor="egoagentos-bridge",
            run=run,
            payload={
                **item["envelope"]["body"],
                "bridge_event_id": item["event_id"],
                "bridge_event_hash": item["event_hash"],
                "evidence_digest": rxp["evidence_digest"],
            },
        )

    tool_evidence = [
        item for item in skill_payload.get("items", []) if item.get("level") == "TOOL_INVOKED"
    ]
    if not tool_evidence:
        raise BridgeError(
            "skill_invocation_trace_missing",
            "AgentTeams spawn message stream contains no successful tool invocation",
        )
    for item in tool_evidence:
        _trace_event(
            events,
            event_type="skill.invoked",
            actor=str(item["worker"]),
            run=run,
            payload={
                "tool": item.get("tool"),
                "session_id": item.get("session_id"),
                "message_seq": item.get("message_seq"),
                "source_endpoint": item.get("source_endpoint"),
                "official_response_sha256": item.get("source_sha256"),
                "matrix_root": rxp["matrix_root"],
            },
        )

    approval = by_kind["APPROVAL_GRANTED"][0]
    _trace_event(
        events,
        event_type="human.approved",
        actor=human_id,
        run=run,
        payload={
            "grant_id": rxp["grant_id"],
            "receipt_digest": rxp["receipt_digest"],
            "matrix_root": rxp["matrix_root"],
            "matrix_event_id": checkpoint.get("approval_granted_matrix_event_id"),
            "bridge_event_id": approval["event_id"],
            "bridge_event_hash": approval["event_hash"],
            "approval_token_persisted": False,
        },
    )

    reviewer_tasks = [
        task for task in run.task_graph if task.stage in {"PLAN_REVIEW", "VERIFY"}
    ]
    review_evidence = []
    accepted_contracts = checkpoint.get("accepted_contracts", {})
    for task in reviewer_tasks:
        evidence = accepted_contracts.get(task.task_id, {})
        if evidence.get("independent_review") and evidence.get("review_verdict") == "PASS":
            review_evidence.append((task, evidence))
    if not review_evidence:
        raise BridgeError(
            "independent_review_trace_missing",
            "No reviewer result envelope proves an independent PASS",
        )
    for task, evidence in review_evidence:
        _trace_event(
            events,
            event_type="independent_review.passed",
            actor=task.assigned_worker,
            run=run,
            payload={
                "agentteams_task_id": task.task_id,
                "evidence_digest": rxp["evidence_digest"],
                **evidence,
            },
        )

    terminal = by_kind["TERMINAL"][0]
    _trace_event(
        events,
        event_type="task.completed",
        actor=leader_id,
        run=run,
        payload={
            "project_id": run.agentteams_project_id,
            "bridge_event_id": terminal["event_id"],
            "bridge_event_hash": terminal["event_hash"],
            "agentteams_status": terminal["envelope"]["body"].get("agentteams_status"),
            "matrix_root": rxp["matrix_root"],
        },
    )
    _trace_event(
        events,
        event_type="decision.committed",
        actor=ego_id,
        run=run,
        payload={
            "decision": final_ego_task["decision"],
            "gate_status": gate.get("status"),
            "independent_reviewer": gate.get("independent_reviewer"),
            "evidence_digest": rxp["evidence_digest"],
        },
    )
    observed_types = {event["type"] for event in events}
    if not REQUIRED_TRACE_EVENTS.issubset(observed_types):
        raise BridgeError(
            "trace_event_coverage_incomplete",
            "verified trace does not cover every required lifecycle event",
            details={"missing": sorted(REQUIRED_TRACE_EVENTS - observed_types)},
        )

    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "source": "AgentTeams",
        "execution_mode": "real-agentteams",
        "synthetic": False,
        "external_origin_status": "UNVERIFIED",
        "seed": seed,
        "scenario_id": scenario_id,
        "project_id": run.agentteams_project_id,
        "task_id": run.ego_task_id,
        "correlation_id": run.correlation_id,
        "trace_id": run.trace_id,
        "context_version": run.context_version,
        "agents": agents,
        "principals": principals,
        "events": events,
        "rxp": rxp,
        "bridge": {
            "api_version": checkpoint.get("bridge_api_version"),
            "benchmark_adapter_version": BENCHMARK_ADAPTER_VERSION,
            "endpoint": "/api/v1/agentteams/runs/{run_id}",
            "run_id": run.id,
        },
        "official_contract": {
            "repository": "https://github.com/agentscope-ai/AgentTeams",
            "main_commit": OFFICIAL_MAIN_COMMIT,
            "api_version": "agentteams.io/v1beta1",
            "controller": probe.get("controller"),
        },
        "official_response_identifiers": {
            "project_id": checkpoint.get("project_create_identifier"),
            "project_create_sha256": checkpoint.get("project_create_response_sha256"),
            "matrix_root": checkpoint.get("matrix_root"),
            "approval_matrix_event_id": checkpoint.get("approval_granted_matrix_event_id"),
            "workflow_sha256": [
                snapshot.get("workflow_sha256")
                for snapshot in snapshots
                if snapshot.get("workflow_sha256")
            ],
        },
        "snapshots": snapshots,
        "bridge_event_chain": bridge_event_chain,
        "replay": checkpoint.get(
            "benchmark_replay",
            {"run_ids": [], "semantic_digests": []},
        ),
        "truth_boundary": (
            "The recomputed bridge ledger proves content integrity only; external AgentTeams "
            "origin remains UNVERIFIED and dry-run fixtures are not runtime evidence."
        ),
    }


def run_scenario(scenario: Any, seed: int, workspace: str | Path) -> Dict[str, Any]:
    """Public benchmark capability, fail-closed until the fault harness exists."""

    scenario_id = str(_value(scenario, "id", _value(scenario, "name", "unknown")))
    if os.getenv("AGENTTEAMS_BENCHMARK_LIVE") != "1":
        return {
            "status": "skip",
            "score": None,
            "details": {
                "execution_mode": "agentteams-unavailable",
                "reason": "Set AGENTTEAMS_BENCHMARK_LIVE=1 with real service credentials",
                "synthetic": False,
                "seed": seed,
            },
        }
    # The bridge client and trace verifier are target scaffolding, but a real
    # fault-injection + fresh-replay implementation does not yet exist for each
    # canonical scenario. A generic terminal run is not release evidence.
    return {
        "status": "skip",
        "score": None,
        "details": {
            "execution_mode": "agentteams-live-target-unimplemented",
            "capability_status": "UNIMPLEMENTED",
            "reason": (
                "real per-scenario fault injection and fresh replay harness is "
                "not implemented; live benchmark remains fail-closed"
            ),
            "scenario_id": scenario_id,
            "synthetic": False,
            "seed": seed,
            "truth_boundary": (
                "bridge clients and trace schemas are target scaffolding only; "
                "no live run, token consumption, or release evidence was attempted"
            ),
        },
    }


def _run_scenario_target_scaffold(
    scenario: Any, seed: int, workspace: str | Path
) -> Dict[str, Any]:
    """Draft live path retained for integration work; not a benchmark capability."""

    scenario_id = str(_value(scenario, "id", _value(scenario, "name", "unknown")))
    binding = _live_binding(scenario, scenario_id)
    task_id = str(binding.get("ego_task_id") or "")
    objective = str(binding.get("objective") or "")
    team = str(_value(scenario, "team", "ego-researchops"))
    if not task_id or not objective:
        return {
            "status": "skip",
            "score": None,
            "details": {
                "execution_mode": "agentteams-live-unconfigured",
                "reason": (
                    "canonical scenarios need a real per-scenario Ego task binding; "
                    "set AGENTTEAMS_BENCHMARK_BINDINGS_FILE"
                ),
                "scenario_id": scenario_id,
                "agent_roles": [],
                "synthetic": False,
            },
        }
    timeout_seconds = int(_value(scenario, "timeout_seconds", 1800))
    approval_token = binding.get("approval_token")
    service = build_service()
    started = time.monotonic()
    snapshots: List[Dict[str, Any]] = []
    try:
        probe = service.probe_live(team)
        run = service.start_run(
            StartRunRequest(
                ego_task_id=task_id,
                objective=objective,
                team=team,
                trace_id="bench-%d-%s" % (seed, hashlib.sha256(task_id.encode()).hexdigest()[:12]),
                correlation_id="bench-corr-%d-%s"
                % (seed, hashlib.sha256(objective.encode()).hexdigest()[:12]),
            )
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = service.reconcile(run.id)
            run = result.run
            snapshots.append(
                {
                    "state": run.state.value,
                    "workflow_sha256": result.workflow_sha256,
                    "actions": result.actions,
                }
            )
            if run.state == RunState.WAITING_R2:
                if not approval_token:
                    return {
                        "status": "error",
                        "score": None,
                        "details": {
                            "execution_mode": "real-agentteams",
                            "reason": "live run reached R2 but scenario supplied no scoped token",
                            "run_id": run.id,
                            "agent_roles": sorted(
                                {task.assigned_worker for task in run.task_graph}
                            ),
                        },
                    }
                run = service.grant_r2(
                    run.id,
                    GrantRequest(
                        approval_token=str(approval_token),
                        idempotency_key="bench-r2-%d-%s" % (seed, run.id[-12:]),
                    ),
                )
            if run.state == RunState.COMPLETED:
                break
            if run.state in {RunState.BLOCKED, RunState.COMPENSATION_REQUIRED}:
                raise BridgeError(
                    "benchmark_run_not_terminal_success",
                    "live bridge entered %s" % run.state.value,
                    details={"run_id": run.id},
                )
            time.sleep(float(_value(scenario, "poll_seconds", 5)))
        if run.state != RunState.COMPLETED:
            raise BridgeError(
                "benchmark_timeout",
                "live AgentTeams benchmark timed out",
                details={"run_id": run.id, "timeout_seconds": timeout_seconds},
            )
        trace = _build_verified_trace(
            service,
            run,
            seed=seed,
            scenario_id=scenario_id,
            probe=probe,
            snapshots=snapshots,
        )
        roles = sorted({agent["role"] for agent in trace["agents"]})
        scenario_verified = _bind_scenario_proof(trace, scenario_id)
        trace_path, trace_sha256 = _write_trace(Path(workspace), trace)
        if not scenario_verified:
            return {
                "status": "error",
                "score": None,
                "latency_seconds": time.monotonic() - started,
                "details": {
                    "execution_mode": "real-agentteams",
                    "reason": "live run did not prove the requested benchmark scenario",
                    "scenario_id": scenario_id,
                    "missing_event_types": trace["scenario_proof"][
                        "missing_event_types"
                    ],
                    "agent_roles": roles,
                    "run_id": run.id,
                    "project_id": run.agentteams_project_id,
                    "agentteams_trace_path": trace_path,
                    "trace_sha256": trace_sha256,
                    "synthetic": False,
                },
            }
        return {
            "status": "pass",
            "score": 1.0,
            "latency_seconds": time.monotonic() - started,
            "details": {
                "execution_mode": "real-agentteams",
                "agent_roles": roles,
                "run_id": run.id,
                "project_id": run.agentteams_project_id,
                "agentteams_trace_path": trace_path,
                "trace_sha256": trace_sha256,
                "synthetic": False,
            },
        }
    except BridgeError as error:
        return {
            "status": "error",
            "score": None,
            "details": {
                "execution_mode": "real-agentteams",
                "reason": error.message,
                "error": error.as_dict(),
                "synthetic": False,
            },
        }
