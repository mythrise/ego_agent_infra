"""Adapter that measures the repository's current deterministic control-plane code."""

from __future__ import annotations

import threading
import time
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from apps.api.errors import ConflictError, ControlPlaneError, PolicyError
from apps.api.evidence import REQUIRED_FOR_DECISION, evidence_gate
from apps.api.models import EvidenceKind, EvidenceRecord, GateStatus, RiskLevel, Stage
from apps.api.policy import (
    build_approval,
    consume_approval,
    decide_approval,
    validate_approval_token,
)
from apps.api.provenance import canonical_sha256
from apps.api.service import DEMO_TASK_ID, ResearchOpsService
from apps.api.store import SQLiteStore
from benchmarks.model import Observation, Scenario
from benchmarks.profiles.base import Profile


class DeterministicCoreProfile(Profile):
    name = "deterministic-core-v0.1"
    description = "Current SQLite state machine, approval policy, evidence gate, and audit chain."

    _unsupported = {
        "worker_timeout_reassign": "current core has no worker lease or dynamic reassignment API",
        "skill_version_rollback": "current core has no versioned skill registry or rollback API",
        "matrix_cherry_pick": "current core has no pre-committed experiment-matrix protocol",
        "matrix_missing_seed": "current core has no pre-committed experiment-matrix protocol",
    }

    def run(
        self,
        scenario: Scenario,
        seed: int,
        repetition: int,
        workspace: Path,
    ) -> Observation:
        if scenario.id in self._unsupported:
            return Observation.skipped(
                self.name,
                scenario,
                repetition,
                seed,
                self._unsupported[scenario.id],
                "benchmarks.profiles.deterministic_core.DeterministicCoreProfile",
            )
        handlers: Dict[str, Callable[[Path, int], Dict[str, Any]]] = {
            "happy_path": self._happy_path,
            "plan_conflict": self._plan_conflict,
            "stale_context": self._stale_context,
            "token_replay": self._token_replay,
            "token_expiry": self._token_expiry,
            "token_scope_mismatch": self._token_scope_mismatch,
            "concurrent_duplicate": self._concurrent_duplicate,
            "crash_recovery": self._crash_recovery,
            "evidence_tamper": self._evidence_tamper,
            "forged_reviewer": self._forged_reviewer,
        }
        handler = handlers.get(scenario.id)
        if handler is None:
            return Observation.skipped(
                self.name,
                scenario,
                repetition,
                seed,
                "no benchmark adapter is registered for this scenario",
                "benchmarks.profiles.deterministic_core.DeterministicCoreProfile",
            )
        started = time.perf_counter_ns()
        try:
            result = handler(workspace, seed)
        except Exception as error:  # a benchmark failure must remain a result, not disappear
            result = {
                "status": "error",
                "operation_count": 0,
                "reason": "%s: %s" % (type(error).__name__, str(error)),
                "assertions": ["profile raised an unexpected exception"],
                "details": {"exception_type": type(error).__name__},
            }
        latency_ms = (time.perf_counter_ns() - started) / 1_000_000
        result.update(
            {
                "profile": self.name,
                "scenario_id": scenario.id,
                "repetition": repetition,
                "seed": seed,
                "latency_ms": latency_ms,
                "implementation_path": (
                    "benchmarks.profiles.deterministic_core.DeterministicCoreProfile"
                ),
                "external_cost_usd": None,
            }
        )
        return Observation(**result)

    @staticmethod
    def _complete_run(database: Path) -> Tuple[Dict[str, Any], bool, List[Dict[str, Any]]]:
        service = ResearchOpsService(SQLiteStore(str(database)))
        service.reset_demo("happy_path")
        paused = service.autorun(DEMO_TASK_ID)
        approval = paused["task"]["pending_approval"]
        bypass_blocked = False
        try:
            service.advance(DEMO_TASK_ID)
        except PolicyError as error:
            bypass_blocked = error.code == "approval_required"
        decision = service.decide_approval(
            approval["id"], "approved", "benchmark-operator", approval["action_digest"]
        )
        completed = service.autorun(DEMO_TASK_ID, decision["approval_token"])
        events = service.events(DEMO_TASK_ID)["events"]
        return completed, bypass_blocked, events

    @staticmethod
    def _semantic_projection(completed: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
        task = completed["task"]
        return {
            "status": completed["status"],
            "stage": task["stage"],
            "decision": task["decision"],
            "gate": task["gate_result"]["status"],
            "evaluation": task["latest_evaluation"],
            "evidence_kinds": sorted(record["kind"] for record in task["evidence"]),
            "event_types": [event["event_type"] for event in events],
        }

    def _happy_path(self, workspace: Path, seed: int) -> Dict[str, Any]:
        first, bypass_blocked, events = self._complete_run(workspace / "happy-a.sqlite3")
        second, second_bypass, second_events = self._complete_run(workspace / "happy-b.sqlite3")
        task = first["task"]
        evidence_present = {record["kind"] for record in task["evidence"]}
        expected_event_types = {
            "research.goal.frozen",
            "plan.review.passed",
            "approval.requested",
            "approval.approved",
            "approval.token.consumed",
            "experiment.submitted",
            "evidence.gate.passed",
            "decision.committed",
            "state.transitioned",
        }
        actual_event_types = {event["event_type"] for event in events}
        projection_a = self._semantic_projection(first, events)
        projection_b = self._semantic_projection(second, second_events)
        digest_a = canonical_sha256(projection_a)
        digest_b = canonical_sha256(projection_b)
        consumed = sum(event["event_type"] == "approval.token.consumed" for event in events)
        passed = all(
            [
                first["status"] == "completed",
                task["stage"] == "COMPLETED",
                task["gate_result"]["status"] == "pass",
                bypass_blocked,
                second_bypass,
                consumed == 1,
                digest_a == digest_b,
            ]
        )
        return {
            "status": "pass" if passed else "fail",
            "operation_count": len(events) + len(second_events),
            "task_completed": first["status"] == "completed",
            "unsafe_action_blocked": bypass_blocked,
            "approval_bypass_succeeded": not bypass_blocked,
            "exactly_once": consumed == 1,
            "trace_completeness": len(expected_event_types & actual_event_types)
            / len(expected_event_types),
            "evidence_completeness": len(evidence_present)
            / len(REQUIRED_FOR_DECISION),
            "reproducible": projection_a == projection_b,
            "hash_agreement": digest_a == digest_b,
            "assertions": [
                "unapproved EXECUTE transition was rejected",
                "all seven evidence classes reached the gate",
                "two independent runs produced the same semantic digest",
            ],
            "details": {
                "semantic_digest_a": digest_a,
                "semantic_digest_b": digest_b,
                "approval_consumed_events": consumed,
                "audit_chain_valid": ResearchOpsService(
                    SQLiteStore(str(workspace / "happy-a.sqlite3"))
                ).events(DEMO_TASK_ID)["chain_valid"],
            },
        }

    @staticmethod
    def _plan_conflict(workspace: Path, seed: int) -> Dict[str, Any]:
        service = ResearchOpsService(SQLiteStore(str(workspace / "plan-conflict.sqlite3")))
        blocked = False
        code = None
        try:
            service.advance(DEMO_TASK_ID, Stage.PLAN)
        except ConflictError as error:
            blocked = True
            code = error.code
        task = service.store.get_task(DEMO_TASK_ID)
        return {
            "status": "pass" if blocked and task.stage == Stage.INTAKE else "fail",
            "operation_count": len(service.store.list_events(task.id, task.generation, limit=1000)),
            "unsafe_action_blocked": blocked,
            "assertions": ["INTAKE to PLAN jump rejected"],
            "details": {"error_code": code, "surviving_stage": task.stage.value},
        }

    @staticmethod
    def _stale_context(workspace: Path, seed: int) -> Dict[str, Any]:
        store = SQLiteStore(str(workspace / "stale.sqlite3"))
        service = ResearchOpsService(store)
        stale = store.get_task(DEMO_TASK_ID)
        old_version = stale.version
        service.advance(DEMO_TASK_ID, Stage.CONTEXT)
        stale.version += 1
        blocked = False
        code = None
        try:
            store.save_task(stale, expected_version=old_version)
        except ConflictError as error:
            blocked = True
            code = error.code
        current = store.get_task(DEMO_TASK_ID)
        passed = blocked and current.stage == Stage.CONTEXT and current.version > old_version
        return {
            "status": "pass" if passed else "fail",
            "operation_count": 2,
            "unsafe_action_blocked": blocked,
            "assertions": ["optimistic version check rejected stale state"],
            "details": {
                "error_code": code,
                "old_version": old_version,
                "current_version": current.version,
            },
        }

    @staticmethod
    def _approved_token(now: datetime) -> Tuple[Any, str, Dict[str, Any]]:
        expected = {
            "task_id": "task-a",
            "generation": "gen-a",
            "scope": "task:task-a:generation:gen-a:experiment",
            "action": "launch",
            "expected_digest": "a" * 64,
        }
        approval = build_approval(
            approval_id="approval-benchmark",
            task_id=expected["task_id"],
            generation=expected["generation"],
            risk_level=RiskLevel.R2,
            scope=expected["scope"],
            action=expected["action"],
            digest=expected["expected_digest"],
            config_sha256="c" * 64,
            action_payload={"config_sha256": "c" * 64},
            now=now,
            ttl_seconds=60,
        )
        approval, token = decide_approval(
            approval, "approved", "benchmark-operator", "a" * 64, now=now
        )
        assert token is not None
        return approval, token, expected

    def _token_replay(self, workspace: Path, seed: int) -> Dict[str, Any]:
        now = datetime(2026, 8, 29, tzinfo=timezone.utc)
        approval, token, expected = self._approved_token(now)
        validate_approval_token(approval, token, now=now + timedelta(seconds=1), **expected)
        effect_database = workspace / "token-replay-effects.sqlite3"
        with sqlite3.connect(effect_database) as connection:
            connection.execute(
                "CREATE TABLE effects(idempotency_key TEXT PRIMARY KEY, payload_digest TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO effects(idempotency_key, payload_digest) VALUES (?, ?)",
                ("token-replay-action", expected["expected_digest"]),
            )
        consume_approval(approval, now + timedelta(seconds=2))
        blocked = False
        code = None
        try:
            validate_approval_token(approval, token, now=now + timedelta(seconds=3), **expected)
        except PolicyError as error:
            blocked = True
            code = error.code
        with sqlite3.connect(effect_database) as connection:
            action_effect_count = int(connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0])
        return {
            "status": (
                "pass"
                if blocked and code == "approval_token_replayed" and action_effect_count == 1
                else "fail"
            ),
            "operation_count": 2,
            "unsafe_action_blocked": blocked,
            "approval_bypass_succeeded": not blocked,
            "exactly_once": blocked and action_effect_count == 1,
            "assertions": ["single-use token rejected on second presentation"],
            "details": {"error_code": code, "action_effect_count": action_effect_count},
        }

    def _token_expiry(self, workspace: Path, seed: int) -> Dict[str, Any]:
        now = datetime(2026, 8, 29, tzinfo=timezone.utc)
        approval, token, expected = self._approved_token(now)
        blocked = False
        code = None
        try:
            validate_approval_token(approval, token, now=now + timedelta(seconds=61), **expected)
        except PolicyError as error:
            blocked = True
            code = error.code
        return {
            "status": "pass" if blocked and code == "approval_token_expired" else "fail",
            "operation_count": 1,
            "unsafe_action_blocked": blocked,
            "approval_bypass_succeeded": not blocked,
            "assertions": ["expired token rejected at the action boundary"],
            "details": {"error_code": code},
        }

    def _token_scope_mismatch(self, workspace: Path, seed: int) -> Dict[str, Any]:
        now = datetime(2026, 8, 29, tzinfo=timezone.utc)
        approval, token, expected = self._approved_token(now)
        expected["task_id"] = "task-b"
        blocked = False
        code = None
        try:
            validate_approval_token(approval, token, now=now + timedelta(seconds=1), **expected)
        except PolicyError as error:
            blocked = True
            code = error.code
        return {
            "status": "pass" if blocked and code == "approval_scope_mismatch" else "fail",
            "operation_count": 1,
            "unsafe_action_blocked": blocked,
            "approval_bypass_succeeded": not blocked,
            "assertions": ["task scope mismatch rejected"],
            "details": {"error_code": code},
        }

    @staticmethod
    def _concurrent_duplicate(workspace: Path, seed: int) -> Dict[str, Any]:
        database = str(workspace / "concurrent.sqlite3")
        first = ResearchOpsService(SQLiteStore(database))
        second = ResearchOpsService(SQLiteStore(database))
        barrier = threading.Barrier(2)

        def advance(service: ResearchOpsService) -> Tuple[str, str]:
            barrier.wait()
            try:
                service.advance(DEMO_TASK_ID, Stage.CONTEXT)
                return "ok", "advanced"
            except ControlPlaneError as error:
                return "blocked", error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(advance, (first, second)))
        task = first.store.get_task(DEMO_TASK_ID)
        evidence = first.store.list_evidence(task.id, task.generation)
        events = first.store.list_events(task.id, task.generation, limit=1000)
        transitions = [
            event
            for event in events
            if event.event_type == "state.transitioned"
            and event.payload.get("from") == "INTAKE"
            and event.payload.get("to") == "CONTEXT"
        ]
        manifest_count = sum(
            record.kind == EvidenceKind.DATASET_MANIFEST for record in evidence
        )
        exactly_once = (
            sum(status == "ok" for status, _ in results) == 1
            and len(transitions) == 1
            and manifest_count == 1
        )
        return {
            "status": "pass" if exactly_once else "fail",
            "operation_count": len(results),
            "exactly_once": exactly_once,
            "assertions": ["two concurrent callers committed one logical transition"],
            "details": {
                "results": sorted([list(item) for item in results]),
                "transition_count": len(transitions),
                "dataset_manifest_count": manifest_count,
            },
        }

    @staticmethod
    def _crash_recovery(workspace: Path, seed: int) -> Dict[str, Any]:
        database = str(workspace / "recovery.sqlite3")
        before = ResearchOpsService(SQLiteStore(database))
        before.advance(DEMO_TASK_ID, Stage.CONTEXT)
        before.advance(DEMO_TASK_ID, Stage.PLAN)
        recovery_started = time.perf_counter_ns()
        after = ResearchOpsService(SQLiteStore(database))
        task = after.store.get_task(DEMO_TASK_ID)
        chain_valid = after.store.verify_event_chain(task.id, task.generation)
        mttr_ms = (time.perf_counter_ns() - recovery_started) / 1_000_000
        recovered = task.stage == Stage.PLAN and chain_valid
        return {
            "status": "pass" if recovered else "fail",
            "operation_count": 3,
            "recovered": recovered,
            "mttr_ms": mttr_ms,
            "trace_completeness": 1.0 if chain_valid else 0.0,
            "assertions": ["fresh service restored PLAN stage from SQLite"],
            "details": {"restored_stage": task.stage.value, "audit_chain_valid": chain_valid},
        }

    @staticmethod
    def _record(kind: EvidenceKind, producer: str, payload: Dict[str, Any]) -> EvidenceRecord:
        return EvidenceRecord(
            id="evd-%s" % kind.value,
            task_id="benchmark-task",
            generation="benchmark-generation",
            kind=kind,
            producer_id=producer,
            artifact_digest=canonical_sha256(payload),
            payload=payload,
            synthetic=True,
        )

    @classmethod
    def _complete_evidence(cls, forged: bool = False) -> List[EvidenceRecord]:
        producer = {
            EvidenceKind.CODE: "runtime",
            EvidenceKind.CONFIG: "architect",
            EvidenceKind.DATASET_MANIFEST: "scout",
            EvidenceKind.LOG: "runtime",
            EvidenceKind.METRIC: "evaluator",
            EvidenceKind.TRACE: "runtime",
        }
        records = []
        for kind in REQUIRED_FOR_DECISION - {EvidenceKind.REVIEW}:
            payload: Dict[str, Any] = {"kind": kind.value, "raw": True}
            if kind == EvidenceKind.METRIC:
                payload = {
                    "deterministic": True,
                    "summary_only": False,
                    "raw_samples": {"baseline": [1.0], "candidate": [2.0]},
                }
            records.append(cls._record(kind, producer[kind], payload))
        reviewer = "runtime" if forged else "reviewer"
        review_payload = {
            "reviewer_id": reviewer,
            "reviewed_producers": sorted(set(producer.values())),
            "independent": True,
            "verdict": "PASS",
        }
        records.append(cls._record(EvidenceKind.REVIEW, reviewer, review_payload))
        return records

    @classmethod
    def _evidence_tamper(cls, workspace: Path, seed: int) -> Dict[str, Any]:
        records = cls._complete_evidence()
        target = next(record for record in records if record.kind == EvidenceKind.LOG)
        target.payload["tampered"] = True
        gate = evidence_gate(records)
        blocked = gate.status == GateStatus.FAIL and any(
            "digest mismatch" in reason for reason in gate.reasons
        )
        return {
            "status": "pass" if blocked else "fail",
            "operation_count": len(records),
            "unsafe_action_blocked": blocked,
            "evidence_completeness": len(gate.present) / len(REQUIRED_FOR_DECISION),
            "assertions": ["payload mutation invalidated the evidence gate"],
            "details": {"gate_status": gate.status.value, "reasons": gate.reasons},
        }

    @classmethod
    def _forged_reviewer(cls, workspace: Path, seed: int) -> Dict[str, Any]:
        records = cls._complete_evidence(forged=True)
        gate = evidence_gate(records)
        blocked = gate.status == GateStatus.FAIL and any(
            "independent PASS review" in reason for reason in gate.reasons
        )
        return {
            "status": "pass" if blocked else "fail",
            "operation_count": len(records),
            "unsafe_action_blocked": blocked,
            "evidence_completeness": len(gate.present) / len(REQUIRED_FOR_DECISION),
            "assertions": ["producing agent could not self-attest independence"],
            "details": {"gate_status": gate.status.value, "reasons": gate.reasons},
        }
