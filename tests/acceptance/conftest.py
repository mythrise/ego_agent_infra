from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

from benchmarks.model import canonical_json, load_corpus
from protocols.rxp import (
    ArtifactRef,
    Decision,
    DeterminismLevel,
    Evidence,
    GrantSigner,
    InMemoryReplayRegistry,
    Intent,
    MatrixAxis,
    MatrixCellDefinition,
    MatrixLedger,
    MatrixPlan,
    Receipt,
    ResourceBounds,
    ResourceRequest,
    ResourceUsage,
    RunManifest,
    canonical_bytes,
    digest_document,
)
from semifinal_acceptance.bundle import (
    AGENTTEAMS_RECEIPTS_SCHEMA_VERSION,
    DECISION_SCHEMA_VERSION,
    EVIDENCE_GATE_SCHEMA_VERSION,
    EVIDENCE_KINDS,
    FROZEN_INPUT_SCHEMA_VERSION,
    INPUT_SCHEMA_VERSION,
    MVP_SCENARIOS,
    RECOVERY_SCHEMA_VERSION,
    REVIEW_SCHEMA_VERSION,
    decision_policy_digest,
    matrix_events_digest,
)
from tests.benchmarks.trace_fixture import build_trace


def sha_bytes(payload: bytes) -> str:
    return "sha256:%s" % hashlib.sha256(payload).hexdigest()


def sha_label(label: str) -> str:
    return sha_bytes(label.encode("utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def write_jsonl(path: Path, values: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(canonical_json(value) + "\n" for value in values), encoding="utf-8"
    )


def _replace(value: Any, old: Any, new: Any) -> Any:
    if isinstance(value, dict):
        return {key: _replace(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, old, new) for item in value]
    return new if value == old else value


def live_trace(
    scenario_id: str,
    seed: int,
    rxp: Optional[Dict[str, str]] = None,
    decision_binding: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    scenario = next(item for item in load_corpus().scenarios if item.id == scenario_id)
    trace = build_trace(scenario, seed)
    trace["truth_boundary"] = (
        "Captured live AgentTeams test contract; content hashes do not authenticate origin."
    )
    trace["official_contract"]["controller"] = "agentteams-controller-live-test"
    if rxp is not None:
        old_rxp = copy.deepcopy(trace["rxp"])
        trace["events"] = _replace(trace["events"], old_rxp["matrix_root"], rxp["matrix_root"])
        for event in trace["events"]:
            if event["type"] == "task.created":
                event["payload"]["intent_digest"] = rxp["intent_digest"]
            if event["type"] == "human.approved":
                event["payload"]["grant_id"] = rxp["grant_id"]
                event["payload"]["receipt_digest"] = rxp["receipt_digest"]
            if event["type"] in {"independent_review.passed", "decision.committed"}:
                event["payload"]["evidence_digest"] = rxp["evidence_digest"]
        trace["rxp"] = rxp
        trace["official_response_identifiers"]["matrix_root"] = rxp["matrix_root"]
    if decision_binding is not None:
        for event in trace["events"]:
            if event["type"] == "decision.committed":
                event["payload"].update(decision_binding)
    return trace


def _official_receipts(
    source: Path, traces: Dict[str, Dict[str, Any]]
) -> Tuple[Path, Dict[str, str]]:
    receipts = []
    ids: Dict[str, str] = {}
    base_kinds = [
        "project_create",
        "workflow_snapshot",
        "delegation",
        "ack",
        "submission",
        "acceptance",
        "spawn",
        "tool_result",
        "terminal",
    ]
    sequence = 0
    for scenario_id in MVP_SCENARIOS:
        trace = traces[scenario_id]
        kinds = list(base_kinds)
        if scenario_id == "worker_timeout_reassign":
            kinds.extend(["cancel", "replan"])
        elif scenario_id == "plan_conflict":
            kinds.append("replan")
        for kind in kinds:
            sequence += 1
            receipt_id = "receipt-%s-%s" % (
                scenario_id.replace("_", "-"),
                kind.replace("_", "-"),
            )
            relative = "agentteams/raw/%s/%02d-%s.json" % (
                scenario_id,
                sequence,
                kind,
            )
            endpoint = "/api/v1/%s" % kind.replace("_", "-")
            request_id = "request-%02d" % sequence
            response_id = "response-%02d" % sequence
            captured_at = "2026-08-29T01:%02d:00Z" % (sequence % 60)
            raw = {
                "schema_version": "egoagentos.agentteams-http-response/v1",
                "scenario_id": scenario_id,
                "kind": kind,
                "request_id": request_id,
                "response_id": response_id,
                "project_id": trace["project_id"],
                "task_id": trace["task_id"],
                "correlation_id": trace["correlation_id"],
                "method": "POST",
                "endpoint": endpoint,
                "status_code": 200,
                "captured_at": captured_at,
                "body": {"ok": True},
            }
            raw_path = source / relative
            write_json(raw_path, raw)
            receipts.append(
                {
                    "receipt_id": receipt_id,
                    "scenario_id": scenario_id,
                    "kind": kind,
                    "source": "official-agentteams-api",
                    "synthetic": False,
                    "project_id": trace["project_id"],
                    "task_id": trace["task_id"],
                    "correlation_id": trace["correlation_id"],
                    "method": "POST",
                    "endpoint": endpoint,
                    "request_id": request_id,
                    "response_id": response_id,
                    "status_code": 200,
                    "captured_at": captured_at,
                    "raw_file": relative,
                    "raw_sha256": sha_bytes(raw_path.read_bytes()),
                }
            )
            ids["%s:%s" % (scenario_id, kind)] = receipt_id
    path = source / "agentteams/receipts.json"
    write_json(
        path,
        {"schema_version": AGENTTEAMS_RECEIPTS_SCHEMA_VERSION, "receipts": receipts},
    )
    return path, ids


def _matrix_records_for_traces(traces: Dict[str, Dict[str, Any]]) -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = []
    event_index = 0
    for scenario_id in MVP_SCENARIOS:
        trace = traces[scenario_id]
        matrix_types = ["TASK_REQUEST", "APPROVAL_GRANTED", "TERMINAL"]
        if scenario_id == "worker_timeout_reassign":
            matrix_types.insert(2, "FAILURE_RECOVERY")
        for sequence, event_type in enumerate(matrix_types, start=1):
            event_index += 1
            trace_rxp = trace["rxp"]
            room_id = "!room-%s:example.org" % scenario_id.replace("_", "-")
            content = {
                "event_type": event_type,
                "scenario_id": scenario_id,
                "task_id": trace["task_id"],
                "correlation_id": trace["correlation_id"],
                "matrix_root": trace_rxp["matrix_root"],
            }
            event_id = "$event-%d" % event_index
            if event_type == "TASK_REQUEST":
                sender = "@bridge:example.org"
                content["intent_digest"] = trace_rxp["intent_digest"]
            elif event_type == "APPROVAL_GRANTED":
                sender = "@human-approver:example.org"
                event_id = trace["official_response_identifiers"][
                    "approval_matrix_event_id"
                ]
                content.update(
                    {
                        "grant_id": trace_rxp["grant_id"],
                        "receipt_digest": trace_rxp["receipt_digest"],
                        "approval_event_id": event_id,
                    }
                )
            elif event_type == "FAILURE_RECOVERY":
                sender = "@worker-runtime:example.org"
                reassignment = next(
                    item for item in trace["events"] if item["type"] == "task.reassigned"
                )["payload"]
                content.update(
                    {
                        "old_worker_id": reassignment["from_assignee"],
                        "new_worker_id": reassignment["to_assignee"],
                        "effect_id": next(
                            item
                            for item in trace["events"]
                            if item["type"] == "effect.committed"
                        )["payload"]["effect_id"],
                        "checkpoint_sha256": sha_label("checkpoint"),
                    }
                )
            else:
                sender = "@ego-decision:example.org"
                trace_decision = next(
                    item for item in trace["events"] if item["type"] == "decision.committed"
                )["payload"]
                content.update(
                    {
                        "evidence_digest": trace_rxp["evidence_digest"],
                        "verdict": trace_decision["verdict"],
                    }
                )
            records.append(
                {
                    "sequence": sequence,
                    "event_id": event_id,
                    "scenario_id": scenario_id,
                    "type": event_type,
                    "project_id": trace["project_id"],
                    "task_id": trace["task_id"],
                    "correlation_id": trace["correlation_id"],
                    "room_id": room_id,
                    "sender": sender,
                    "origin_server_ts": 1_000 * event_index,
                    "content": content,
                }
            )
    return records


def _build_rxp(
    source: Path,
    frozen: Dict[str, Any],
    evidence_files: Dict[str, str],
) -> Tuple[Path, Dict[str, Any]]:
    plan = MatrixPlan(
        matrix_id="matrix:semifinal-live-v1",
        name="Bounded live GPU acceptance",
        frozen_by="agent:research-pi",
        frozen_at="2026-08-29T01:00:00Z",
        axes=(MatrixAxis(name="workload", values=("bounded-live",)),),
        cells=(
            MatrixCellDefinition(
                cell_id="cell-live-gpu", coordinates={"workload": "bounded-live"}
            ),
        ),
    )
    ledger = MatrixLedger(plan)
    payload = {"entrypoint": "experiment.execute", "workload": "bounded-live"}
    intent = Intent(
        intent_id="intent:semifinal-live-v1",
        matrix_id=plan.matrix_id,
        cell_id="cell-live-gpu",
        coordinates={"workload": "bounded-live"},
        actor_id="agent:experiment-architect",
        created_at="2026-08-29T01:00:01Z",
        action="experiment.execute",
        scope="matrix:semifinal-live-v1:cell:cell-live-gpu",
        action_payload=payload,
        action_payload_digest=digest_document(payload),
        run_manifest=RunManifest(
            git_commit=frozen["git_commit"],
            config_sha256=frozen["config_digest"],
            dataset_manifest_sha256=frozen["dataset_manifest_digest"],
            environment_lock_sha256=frozen["environment_lock_digest"],
            base_model_sha256=frozen["model_digest"],
            seed=20260829,
        ),
        requested_resources=ResourceRequest(
            gpu_count=1,
            wall_time_seconds=120,
            gpu_time_seconds=60,
            artifact_bytes=32,
        ),
        required_determinism=DeterminismLevel.D2_SEEDED_ENV_BOUND,
        extensions={"data_classification": "OPEN_REPRODUCIBLE"},
    )
    intent_digest = ledger.record_intent(intent)
    signer = GrantSigner(b"acceptance test key material is at least 32 bytes long", key_id="test-retired-key")
    grant = signer.issue(
        intent,
        grant_id="grant:semifinal-live-v1",
        issuer_id="human:approver",
        bounds=ResourceBounds(
            max_gpu_count=1,
            max_wall_time_seconds=300,
            max_gpu_time_seconds=300,
            max_artifact_bytes=4096,
        ),
        minimum_determinism=DeterminismLevel.D2_SEEDED_ENV_BOUND,
        issued_at="2026-08-29T01:00:02Z",
        expires_at="2026-08-29T01:10:02Z",
        nonce="semifinal_live_nonce_0001",
    )
    grant_digest = ledger.record_grant(
        grant, verifier=signer, accepted_at="2026-08-29T01:00:03Z"
    )
    output_path = source / "runtime/output.bin"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"bounded-real-gpu-output")
    receipt = Receipt(
        receipt_id="receipt:semifinal-live-v1",
        matrix_id=plan.matrix_id,
        cell_id="cell-live-gpu",
        intent_digest=intent_digest,
        grant_digest=grant_digest,
        grant_id=grant.claims.grant_id,
        executor_id="agent:runtime",
        started_at="2026-08-29T01:00:04Z",
        completed_at="2026-08-29T01:02:04Z",
        outcome="SUCCEEDED",
        output=ArtifactRef(
            uri="artifact://runtime/output.bin",
            media_type="application/octet-stream",
            sha256=sha_bytes(output_path.read_bytes()),
            bytes=len(output_path.read_bytes()),
        ),
        usage=ResourceUsage(
            gpu_count=1,
            wall_time_seconds=120,
            gpu_time_seconds=120,
            artifact_bytes=len(output_path.read_bytes()),
        ),
        determinism_level=DeterminismLevel.D2_SEEDED_ENV_BOUND,
        replay_count=1,
        metadata={"job_id": "gpu-job-001", "execution_class": "bounded-live"},
    )
    receipt_digest = ledger.record_receipt(receipt, replay_registry=InMemoryReplayRegistry())
    producers = {
        "code": "agent:architect",
        "config": "agent:architect",
        "dataset_manifest": "agent:scout",
        "log": "agent:runtime",
        "metric": "agent:evaluator",
        "trace": "agent:runtime",
        "review": "agent:reviewer",
    }
    non_review = sorted(set(producers.values()) - {producers["review"]})
    for evidence_type in EVIDENCE_KINDS:
        artifact_path = source / evidence_files[evidence_type]
        claims: Dict[str, Any] = {}
        if evidence_type == "metric":
            claims = {
                "deterministic": True,
                "summary_only": False,
                "raw_data_digest": sha_bytes(artifact_path.read_bytes()),
            }
        elif evidence_type == "review":
            claims = {
                "independent": True,
                "reviewed_producers": non_review,
                "reviewer_id": producers["review"],
                "verdict": "PASS",
            }
        ledger.record_evidence(
            Evidence(
                evidence_id="evidence:live:%s:v1" % evidence_type,
                matrix_id=plan.matrix_id,
                cell_id="cell-live-gpu",
                receipt_digest=receipt_digest,
                evidence_type=evidence_type,
                producer_id=producers[evidence_type],
                artifact=ArtifactRef(
                    uri="artifact://%s" % evidence_files[evidence_type],
                    media_type="application/json",
                    sha256=sha_bytes(artifact_path.read_bytes()),
                    bytes=len(artifact_path.read_bytes()),
                ),
                claims=claims,
                observed_at="2026-08-29T01:02:05Z",
            )
        )
    gate = ledger.assess_evidence("cell-live-gpu")
    decision = Decision(
        decision_id="decision:semifinal-live-v1",
        matrix_id=plan.matrix_id,
        cell_id="cell-live-gpu",
        intent_digest=intent_digest,
        receipt_digest=receipt_digest,
        evidence_digests=gate.evidence_digests,
        evidence_root=gate.evidence_root,
        gate=gate,
        verdict="KEEP",
        determinism_level=DeterminismLevel.D2_SEEDED_ENV_BOUND,
        decided_by="agent:research-pi",
        decided_at="2026-08-29T01:02:06Z",
        rationale_code="BOUNDED_GATE_PASS",
    )
    ledger.record_decision(decision)
    snapshot = ledger.snapshot().model_dump(mode="json")
    path = source / "rxp/ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(snapshot) + b"\n")
    entries = [entry for entry in snapshot["entries"] if entry["cell_id"] == "cell-live-gpu"]
    by_kind: Dict[str, Any] = {}
    evidence: Dict[str, Any] = {}
    for entry in entries:
        if entry["document_kind"] == "Evidence":
            evidence[entry["document"]["evidence_type"]] = entry
        else:
            by_kind[entry["document_kind"]] = entry
    return path, {
        "ledger": snapshot,
        "intent_digest": by_kind["Intent"]["document_digest"],
        "grant_id": by_kind["Grant"]["document"]["claims"]["grant_id"],
        "receipt_digest": by_kind["Receipt"]["document_digest"],
        "decision_digest": by_kind["Decision"]["document_digest"],
        "decision": by_kind["Decision"]["document"],
        "evidence": evidence,
        "evidence_root": by_kind["Decision"]["document"]["evidence_root"],
    }


def build_acceptance_source(source: Path) -> Path:
    source.mkdir(parents=True, exist_ok=True)
    config_path = source / "inputs/config.json"
    dataset_path = source / "inputs/dataset-manifest.json"
    code_path = source / "inputs/code-manifest.json"
    write_json(config_path, {"batch_size": 1, "entrypoint": "experiment.execute"})
    write_json(dataset_path, {"dataset": "open-bounded-v1", "sample_ids": ["s1", "s2"]})
    write_json(code_path, {"git_commit": "1" * 40, "entrypoint": "experiment.execute"})
    frozen = {
        "schema_version": FROZEN_INPUT_SCHEMA_VERSION,
        "git_commit": "1" * 40,
        "git_dirty": False,
        "container_image_digest": sha_label("container"),
        "config_digest": sha_bytes(config_path.read_bytes()),
        "environment_lock_digest": sha_label("environment"),
        "dataset_manifest_digest": sha_bytes(dataset_path.read_bytes()),
        "model_digest": sha_label("model"),
        "skill_registry_digest": sha_label("skills"),
        "agentteams_contract_digest": sha_label("agentteams-contract"),
        "metric_contract": {
            "metric_name": "score",
            "scale": 1000,
            "aggregation": "mean",
            "sample_ids": ["s1", "s2"],
            "matrix_cells": [{"cell_id": "cell-live-gpu", "seed": 20260829}],
            "declared_filters": [],
            "decision_policy": {
                "kind": "minimum_mean",
                "cell_id": "cell-live-gpu",
                "minimum_scaled_fraction": "100/1",
                "pass_rationale_code": "BOUNDED_GATE_PASS",
                "fail_rationale_code": "METRIC_THRESHOLD_FAIL",
            },
        },
        "budget": {
            "max_gpu_count": 1,
            "max_gpu_seconds": 300,
            "max_wall_seconds": 300,
            "max_artifact_bytes": 4096,
            "max_retries": 1,
            "currency": "CNY",
            "max_cost_decimal": "20",
        },
    }
    frozen_path = source / "inputs/frozen-inputs.json"
    write_json(frozen_path, frozen)
    policy_digest = decision_policy_digest(frozen["metric_contract"]["decision_policy"])

    raw_metrics_path = source / "metrics/raw.jsonl"
    raw_records = [
        {
            "record_id": "r1",
            "sample_id": "s1",
            "cell_id": "cell-live-gpu",
            "metric_name": "score",
            "value_scaled": 100,
            "included": True,
            "filter_id": None,
        },
        {
            "record_id": "r2",
            "sample_id": "s2",
            "cell_id": "cell-live-gpu",
            "metric_name": "score",
            "value_scaled": 120,
            "included": True,
            "filter_id": None,
        },
    ]
    write_jsonl(raw_metrics_path, raw_records)
    summary_path = source / "metrics/summary.json"
    write_json(
        summary_path,
        {
            "metric_name": "score",
            "scale": 1000,
            "aggregation": "mean",
            "included_n": 2,
            "filtered_n": 0,
            "sum_scaled": 220,
            "mean_scaled_fraction": "110/1",
            "raw_metrics_sha256": sha_bytes(raw_metrics_path.read_bytes()),
        },
    )
    gpu_path = source / "runtime/gpu-metrics.jsonl"
    write_jsonl(
        gpu_path,
        [
            {
                "sequence": 1,
                "timestamp_ns": 1_000_000_000,
                "job_id": "gpu-job-001",
                "gpu_uuid": "GPU-live-001",
                "gpu_model": "RTX 4090",
                "utilization_pct": 10,
                "memory_used_bytes": 1024,
                "power_w": 100,
            },
            {
                "sequence": 2,
                "timestamp_ns": 121_000_000_000,
                "job_id": "gpu-job-001",
                "gpu_uuid": "GPU-live-001",
                "gpu_model": "RTX 4090",
                "utilization_pct": 90,
                "memory_used_bytes": 2048,
                "power_w": 250,
            },
        ],
    )
    execution_trace_path = source / "runtime/execution-trace.json"
    write_json(execution_trace_path, {"job_id": "gpu-job-001", "spans": ["start", "finish"]})
    checkpoint_path = source / "runtime/checkpoint.bin"
    checkpoint_path.write_bytes(b"checkpoint")

    primary_seed = 91
    provisional_traces: Dict[str, Dict[str, Any]] = {}
    for index, scenario_id in enumerate(MVP_SCENARIOS, start=1):
        seed = primary_seed if scenario_id == "worker_timeout_reassign" else 100 + index
        provisional_traces[scenario_id] = live_trace(scenario_id, seed)
    provisional = provisional_traces["worker_timeout_reassign"]
    run = {
        "primary_scenario_id": "worker_timeout_reassign",
        "task_id": provisional["task_id"],
        "project_id": provisional["project_id"],
        "correlation_id": provisional["correlation_id"],
        "trace_id": provisional["trace_id"],
        "rxp_cell_id": "cell-live-gpu",
        "seed": primary_seed,
    }
    receipts_path, receipt_ids = _official_receipts(source, provisional_traces)
    matrix_path = source / "agentteams/matrix-events.jsonl"

    review_path = source / "review/independent-review.json"
    write_json(
        review_path,
        {
            "schema_version": REVIEW_SCHEMA_VERSION,
            "reviewer_id": "agent:reviewer",
            "independent": True,
            "verdict": "PASS",
            "reviewed_producers": [
                "agent:architect",
                "agent:evaluator",
                "agent:runtime",
                "agent:scout",
            ],
            "reviewed_trace_sha256": sha_bytes(execution_trace_path.read_bytes()),
        },
    )
    evidence_files = {
        "code": code_path.relative_to(source).as_posix(),
        "config": config_path.relative_to(source).as_posix(),
        "dataset_manifest": dataset_path.relative_to(source).as_posix(),
        "log": receipts_path.relative_to(source).as_posix(),
        "metric": raw_metrics_path.relative_to(source).as_posix(),
        "trace": execution_trace_path.relative_to(source).as_posix(),
        "review": review_path.relative_to(source).as_posix(),
    }
    rxp_path, rxp = _build_rxp(source, frozen, evidence_files)

    trace_paths: Dict[str, str] = {}
    final_traces: Dict[str, Dict[str, Any]] = {}
    for index, scenario_id in enumerate(MVP_SCENARIOS, start=1):
        seed = primary_seed if scenario_id == "worker_timeout_reassign" else 100 + index
        rxp_binding = None
        if scenario_id == "worker_timeout_reassign":
            rxp_binding = {
                "intent_digest": rxp["intent_digest"],
                "grant_id": rxp["grant_id"],
                "receipt_digest": rxp["receipt_digest"],
                "evidence_digest": rxp["evidence_root"],
                "matrix_root": rxp["ledger"]["root"],
            }
        decision_binding = None
        if scenario_id == "worker_timeout_reassign":
            decision_binding = {
                "verdict": "KEEP",
                "rationale_code": "BOUNDED_GATE_PASS",
                "decision_policy_sha256": policy_digest,
            }
        trace = live_trace(scenario_id, seed, rxp_binding, decision_binding)
        final_traces[scenario_id] = trace
        path = source / ("traces/%s.json" % scenario_id)
        write_json(path, trace)
        trace_paths[scenario_id] = path.relative_to(source).as_posix()
        if scenario_id == "worker_timeout_reassign":
            run.update(
                {
                    "task_id": trace["task_id"],
                    "project_id": trace["project_id"],
                    "correlation_id": trace["correlation_id"],
                    "trace_id": trace["trace_id"],
                }
            )
            primary_trace_path = path

    matrix_records = _matrix_records_for_traces(final_traces)
    write_jsonl(matrix_path, matrix_records)
    matrix_root = matrix_events_digest(matrix_records)
    primary_trace = final_traces["worker_timeout_reassign"]
    primary_decision = next(
        item for item in primary_trace["events"] if item["type"] == "decision.committed"
    )
    primary_decision["payload"]["matrix_events_root"] = matrix_root
    write_json(primary_trace_path, primary_trace)

    evidence_items = []
    for evidence_type in EVIDENCE_KINDS:
        entry = rxp["evidence"][evidence_type]
        artifact_path = source / evidence_files[evidence_type]
        evidence_items.append(
            {
                "kind": evidence_type,
                "producer_id": entry["document"]["producer_id"],
                "artifact_file": evidence_files[evidence_type],
                "artifact_sha256": sha_bytes(artifact_path.read_bytes()),
                "rxp_document_digest": entry["document_digest"],
            }
        )
    gate_path = source / "evidence/evidence-gate.json"
    write_json(
        gate_path,
        {
            "schema_version": EVIDENCE_GATE_SCHEMA_VERSION,
            "status": "PASS",
            "required_kinds": list(EVIDENCE_KINDS),
            "evidence": evidence_items,
            "independent_reviewer_id": "agent:reviewer",
            "evidence_root": rxp["evidence_root"],
        },
    )
    recovery_path = source / "runtime/failure-recovery.json"
    primary_reassignment = next(
        item
        for item in final_traces["worker_timeout_reassign"]["events"]
        if item["type"] == "task.reassigned"
    )["payload"]
    primary_effect = next(
        item
        for item in final_traces["worker_timeout_reassign"]["events"]
        if item["type"] == "effect.committed"
    )["payload"]
    write_json(
        recovery_path,
        {
            "schema_version": RECOVERY_SCHEMA_VERSION,
            "scenario_id": "worker_timeout_reassign",
            "fault_type": "worker_timeout",
            "checkpoint_file": checkpoint_path.relative_to(source).as_posix(),
            "checkpoint_sha256": sha_label("checkpoint"),
            "old_worker_id": primary_reassignment["from_assignee"],
            "new_worker_id": primary_reassignment["to_assignee"],
            "old_worker_fenced": True,
            "checkpoint_restored": True,
            "effect_ids": [primary_effect["effect_id"]],
            "idempotency_key": primary_effect["idempotency_key"],
            "recovery_started_at_ns": 1_000_000_000,
            "recovery_completed_at_ns": 1_250_000_000,
            "mttr_ms": 250,
            "official_receipt_ids": [
                receipt_ids["worker_timeout_reassign:cancel"],
                receipt_ids["worker_timeout_reassign:replan"],
            ],
            "scheduler_fence_receipt_id": receipt_ids[
                "worker_timeout_reassign:cancel"
            ],
        },
    )
    primary_trace_root = sha_bytes(primary_trace_path.read_bytes())
    decision_path = source / "decision/decision.json"
    write_json(
        decision_path,
        {
            "schema_version": DECISION_SCHEMA_VERSION,
            "decided_by": "agent:research-pi",
            "verdict": "KEEP",
            "gate_status": "PASS",
            "trace_root": primary_trace_root,
            "evidence_root": rxp["evidence_root"],
            "rxp_decision_digest": rxp["decision_digest"],
            "rationale_code": "BOUNDED_GATE_PASS",
            "decision_policy_sha256": policy_digest,
            "matrix_events_root": matrix_root,
        },
    )

    corpus = load_corpus()
    scenario_results = []
    for scenario in corpus.scenarios:
        if scenario.id in MVP_SCENARIOS:
            trace = json.loads((source / trace_paths[scenario.id]).read_text(encoding="utf-8"))
            scenario_results.append(
                {
                    "scenario_id": scenario.id,
                    "status": "PASS",
                    "seed": trace["seed"],
                    "repetition": 0,
                    "trace_file": trace_paths[scenario.id],
                }
            )
        else:
            scenario_results.append(
                {
                    "scenario_id": scenario.id,
                    "status": "SKIP",
                    "reason": "outside the declared eight-scenario semifinal MVP",
                }
            )
    descriptor = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "acceptance_id": "semifinal-live-test-001",
        "created_at": "2026-08-29T01:05:00Z",
        "gate_profile": "mvp-8",
        "truth_boundary": {
            "execution_mode": "real-agentteams",
            "synthetic": False,
            "gpu_execution": "real",
            "external_origin_authentication": "UNVERIFIED_OPERATOR_ASSERTION",
        },
        "run": run,
        "corpus": {
            "benchmark": "rxp-bench/v1",
            "corpus_version": corpus.corpus_version,
            "corpus_digest": corpus.digest,
            "total_scenarios": len(corpus.scenarios),
            "mvp_scenarios": list(MVP_SCENARIOS),
            "mvp_contract_status": "PASS",
            "full_release_status": "NOT_EVALUATED",
        },
        "files": {
            "frozen_inputs": frozen_path.relative_to(source).as_posix(),
            "agentteams_receipts": receipts_path.relative_to(source).as_posix(),
            "matrix_events": matrix_path.relative_to(source).as_posix(),
            "gpu_raw_metrics": gpu_path.relative_to(source).as_posix(),
            "raw_metrics": raw_metrics_path.relative_to(source).as_posix(),
            "metric_summary": summary_path.relative_to(source).as_posix(),
            "rxp_ledger": rxp_path.relative_to(source).as_posix(),
            "evidence_gate": gate_path.relative_to(source).as_posix(),
            "failure_recovery": recovery_path.relative_to(source).as_posix(),
            "checkpoint": checkpoint_path.relative_to(source).as_posix(),
            "review": review_path.relative_to(source).as_posix(),
            "decision": decision_path.relative_to(source).as_posix(),
        },
        "scenario_results": scenario_results,
    }
    write_json(source / "acceptance-input.json", descriptor)
    return source


@pytest.fixture
def acceptance_source(tmp_path: Path) -> Path:
    return build_acceptance_source(tmp_path / "source")
