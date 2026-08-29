"""Deterministic test-only fixtures for the independent trace verifier."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from benchmarks.model import Scenario, canonical_json, canonical_sha256
from benchmarks.trace_verifier import SCENARIO_REQUIRED_EVENTS


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def build_trace(scenario: Scenario, seed: int) -> Dict[str, Any]:
    task_id = "ego-task-%s" % scenario.id
    project_id = "project-%s-%d" % (scenario.id, seed)
    correlation_id = "corr-%s-%d" % (scenario.id, seed)
    trace_id = "trace-%s-%d" % (scenario.id, seed)
    bridge_run_id = "run-%s-%d" % (scenario.id, seed)
    intent_digest = digest("intent:%s:%d" % (scenario.id, seed))
    receipt_digest = digest("receipt:%s:%d" % (scenario.id, seed))
    evidence_digest = digest("evidence:%s:%d" % (scenario.id, seed))
    matrix_root = "matrix-event-%s-%d" % (scenario.id, seed)
    workflow_digest = digest("workflow:%s:%d" % (scenario.id, seed))
    bridge_items: List[Dict[str, Any]] = []

    def bridge_item(kind: str, body: Dict[str, Any]) -> Dict[str, Any]:
        sequence = len(bridge_items) + 1
        created_at = "2026-08-29T00:00:%02d+00:00" % sequence
        envelope = {
            "schema": "egoagentos.agentteams-envelope.v2",
            "envelope_id": "env-%s-%d-%02d" % (scenario.id, seed, sequence),
            "task_id": task_id,
            "project_id": project_id,
            "trace_id": trace_id,
            "correlation_id": correlation_id,
            "context_version": 1,
            "attempt": 1,
            "kind": kind,
            "sender": "bridge-main",
            "recipient": "matrix-executor",
            "causation_id": None,
            "body": body,
            "body_sha256": canonical_sha256(body),
            "created_at": created_at,
        }
        previous_hash = bridge_items[-1]["event_hash"] if bridge_items else "0" * 64
        event_id = "evt-%s-%d-%02d" % (scenario.id, seed, sequence)
        hash_payload = {
            "event_id": event_id,
            "run_id": bridge_run_id,
            "kind": kind,
            "envelope": envelope,
            "previous_hash": previous_hash,
            "created_at": created_at,
        }
        item = {
            "sequence": sequence,
            **hash_payload,
            "event_hash": hashlib.sha256(
                canonical_json(hash_payload).encode("utf-8")
            ).hexdigest(),
        }
        bridge_items.append(item)
        return item

    created_bridge = bridge_item("TASK_REQUEST", {"objective": "bounded verifier contract"})
    delegated_bridge = bridge_item(
        "TASK_UPDATE", {"status": "delegated", "assignee": "matrix-executor"}
    )
    accepted_bridge = bridge_item(
        "ARTIFACT_ACCEPTED", {"artifact_sha256": digest("accepted")}
    )
    approval_bridge = bridge_item(
        "APPROVAL_GRANTED", {"risk_level": "R2", "approval_token_persisted": False}
    )
    terminal_bridge = bridge_item("TERMINAL", {"agentteams_status": "COMPLETED"})
    chain_head = terminal_bridge["event_hash"]
    events: List[Dict[str, Any]] = []

    def event(event_type: str, actor: str, payload: Dict[str, Any]) -> None:
        events.append(
            {
                "sequence": len(events) + 1,
                "type": event_type,
                "actor": actor,
                "task_id": task_id,
                "correlation_id": correlation_id,
                "payload": payload,
            }
        )

    event(
        "task.created",
        "bridge-main",
        {
            "intent_digest": intent_digest,
            "matrix_root": matrix_root,
            "bridge_event_id": created_bridge["event_id"],
            "bridge_event_hash": created_bridge["event_hash"],
        },
    )
    event(
        "task.delegated",
        "bridge-main",
        {
            "assignee": "matrix-executor",
            "bridge_event_id": delegated_bridge["event_id"],
            "bridge_event_hash": delegated_bridge["event_hash"],
        },
    )
    event(
        "task.accepted",
        "bridge-main",
        {
            "artifact_sha256": digest("accepted"),
            "bridge_event_id": accepted_bridge["event_id"],
            "bridge_event_hash": accepted_bridge["event_hash"],
        },
    )
    event(
        "skill.invoked",
        "worker-executor",
        {
            "tool": "experiment.execute",
            "session_id": "session-%d" % seed,
            "message_seq": 1,
            "source_endpoint": "/api/v1/spawns/messages",
            "official_response_sha256": digest("skill-response:%d" % seed),
            "matrix_root": matrix_root,
        },
    )
    event(
        "human.approved",
        "human-approver",
        {
            "grant_id": "grant-%s-%d" % (scenario.id, seed),
            "receipt_digest": receipt_digest,
            "matrix_root": matrix_root,
            "bridge_event_id": approval_bridge["event_id"],
            "bridge_event_hash": approval_bridge["event_hash"],
        },
    )
    for event_type in SCENARIO_REQUIRED_EVENTS[scenario.id]:
        actor = "worker-executor" if event_type == "effect.committed" else "bridge-main"
        payload: Dict[str, Any] = {"matrix_root": matrix_root}
        if event_type == "effect.committed":
            payload.update(
                {
                    "effect_id": "effect-%s-%d" % (scenario.id, seed),
                    "idempotency_key": "idem-%s-%d" % (scenario.id, seed),
                }
            )
        if event_type == "task.reassigned":
            payload.update(
                {
                    "from_assignee": "matrix-executor",
                    "to_assignee": "matrix-planner",
                }
            )
        event(event_type, actor, payload)
    event(
        "independent_review.passed",
        "worker-reviewer",
        {"evidence_digest": evidence_digest, "verdict": "PASS"},
    )
    event(
        "task.completed",
        "worker-executor",
        {
            "matrix_root": matrix_root,
            "bridge_event_id": terminal_bridge["event_id"],
            "bridge_event_hash": chain_head,
        },
    )
    event(
        "decision.committed",
        "ego-decision",
        {
            "evidence_digest": evidence_digest,
            "matrix_root": matrix_root,
            "verdict": "KEEP" if scenario.id == "happy_path" else "REJECT",
        },
    )
    replay_digest = digest("replay:%s:%d" % (scenario.id, seed))
    return {
        "schema_version": "egoagentos.agentteams-trace/v1",
        "source": "AgentTeams",
        "execution_mode": "real-agentteams",
        "external_origin_status": "UNVERIFIED",
        "seed": seed,
        "scenario_id": scenario.id,
        "project_id": project_id,
        "task_id": task_id,
        "correlation_id": correlation_id,
        "trace_id": trace_id,
        "context_version": 1,
        "agents": [
            {
                "id": "worker-executor",
                "role": "runtime",
                "matrix_user_id": "matrix-executor",
                "source": "GET /api/v1/workers/runtime",
            },
            {
                "id": "worker-reviewer",
                "role": "reviewer",
                "matrix_user_id": "matrix-reviewer",
                "source": "GET /api/v1/workers/reviewer",
            },
            {
                "id": "worker-planner",
                "role": "planner",
                "matrix_user_id": "matrix-planner",
                "source": "GET /api/v1/workers/planner",
            },
        ],
        "principals": [
            {"id": "bridge-main", "kind": "bridge", "source": "bridge identity"},
            {"id": "human-approver", "kind": "human", "source": "RXP grant"},
            {"id": "ego-decision", "kind": "ego", "source": "Ego task state"},
        ],
        "events": events,
        "rxp": {
            "intent_digest": intent_digest,
            "grant_id": "grant-%s-%d" % (scenario.id, seed),
            "receipt_digest": receipt_digest,
            "evidence_digest": evidence_digest,
            "matrix_root": matrix_root,
        },
        "bridge": {
            "api_version": "v1",
            "endpoint": "/api/v1/agentteams/runs/run-fixture",
            "run_id": bridge_run_id,
        },
        "official_contract": {
            "repository": "https://github.com/agentscope-ai/AgentTeams",
            "main_commit": "223ddc2b8073e4c8b93bcbb15e1d717f196c04d9",
            "api_version": "agentteams.io/v1beta1",
            "controller": "fixture-controller",
        },
        "official_response_identifiers": {
            "project_id": "project-%s-%d" % (scenario.id, seed),
            "project_create_sha256": digest("project-create:%d" % seed),
            "matrix_root": matrix_root,
            "approval_matrix_event_id": "approval-event-%d" % seed,
            "workflow_sha256": [workflow_digest],
        },
        "snapshots": [{"state": "COMPLETED", "workflow_sha256": workflow_digest}],
        "bridge_event_chain": {
            "valid": True,
            "hash_algorithm": "sha256-canonical-json-v1",
            "external_origin_status": "UNVERIFIED",
            "total": len(bridge_items),
            "head": chain_head,
            "source_ledger_total": len(bridge_items),
            "items": bridge_items,
        },
        "replay": {
            "run_ids": ["replay-a-%d" % seed, "replay-b-%d" % seed],
            "semantic_digests": [replay_digest, replay_digest],
        },
        "truth_boundary": "Synthetic verifier fixture; never external runtime evidence.",
    }


def trace_bytes(scenario: Scenario, seed: int) -> bytes:
    return json.dumps(
        build_trace(scenario, seed),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
