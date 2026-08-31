from __future__ import annotations

from typing import Any, Dict

import pytest

from apps.agentteams_bridge.extensions.contracts import (
    CanonicalEffect,
    RiskDisposition,
    RiskLevel,
    SafetyVerdict,
)
from apps.agentteams_bridge.extensions.safety import evaluate_effect_safety
from benchmarks.secure_memory.canonical import canonical_sha256


SHA = "a" * 64
OTHER_SHA = "b" * 64


def _effect(**overrides: Any) -> CanonicalEffect:
    values: Dict[str, Any] = {
        "schema_version": "agentteams-canonical-effect/v1",
        "effect_id": "effect-fail-closed",
        "operation": "workspace.write",
        "final_arguments": {"path": "report.md", "text": "accepted evidence"},
        "target": "workspace/report.md",
        "affected_scope": ("project:project-1", "task:task-1"),
        "project_id": "project-1",
        "task_id": "task-1",
        "workspace_checkpoint_sha256": SHA,
        "policy_sha256": OTHER_SHA,
        "reversibility": "REVERSIBLE",
        "recovery_plan": "REMOVE_CREATED_PATH",
    }
    values.update(overrides)
    values["effect_sha256"] = canonical_sha256("agentteams-canonical-effect", values)
    return CanonicalEffect.model_validate(values)


def _decision(effect: CanonicalEffect) -> Any:
    return evaluate_effect_safety(
        effect,
        sequence=7,
        approval_expires_at_sequence=11,
    )


def _assert_mandatory_deny(effect: CanonicalEffect, reason: str, constraint: str) -> None:
    decision = _decision(effect)
    system = decision.guardian_decision.system_assessment
    guardian = decision.guardian_decision.guardian_assessment

    assert system.risk_level is RiskLevel.HIGH
    assert system.disposition is RiskDisposition.DENY
    assert reason in system.reason_codes
    assert constraint in system.mandatory_constraint_ids
    assert guardian is not None
    assert guardian.disposition is RiskDisposition.DENY
    assert decision.verdict is SafetyVerdict.DENY


@pytest.mark.parametrize(
    "affected_scope",
    [
        ("task:task-1",),
        ("project:project-1",),
        ("component:extra", "project:project-1", "task:task-1"),
        ("project:project-other", "task:task-1"),
        ("project:project-1", "task:task-other"),
    ],
)
def test_missing_extra_or_wrong_project_task_scope_is_mandatory_deny(
    affected_scope: tuple[str, ...],
) -> None:
    _assert_mandatory_deny(
        _effect(affected_scope=affected_scope),
        "CROSS_PROJECT_TARGET",
        "constraint.project-boundary",
    )


def test_review_cross_project_reproducer_cannot_receive_allow() -> None:
    _assert_mandatory_deny(
        _effect(
            target="project-beta/report.md",
            affected_scope=("task:task-1",),
        ),
        "CROSS_PROJECT_TARGET",
        "constraint.project-boundary",
    )


@pytest.mark.parametrize(
    "target",
    [
        "workspace",
        "/workspace/report.md",
        "workspace/../report.md",
        "workspace//report.md",
        "workspace/report.md/",
        "workspace\\report.md",
        "other-root/report.md",
    ],
)
def test_target_must_be_a_canonical_workspace_subpath(target: str) -> None:
    _assert_mandatory_deny(
        _effect(target=target),
        "PATH_ESCAPE",
        "constraint.workspace-boundary",
    )


@pytest.mark.parametrize(
    "target",
    [
        "workspace/evidence",
        "workspace/evidence/decision.json",
        "workspace/receipts",
        "workspace/receipts/control.json",
        "workspace/trace",
        "workspace/trace/events.jsonl",
        "workspace/decision-closure",
        "workspace/requirement-ledger/current.json",
        "workspace/trusted-memory",
    ],
)
def test_evidence_roots_and_subtrees_are_immutable(target: str) -> None:
    _assert_mandatory_deny(
        _effect(target=target),
        "EVIDENCE_TAMPERING",
        "constraint.evidence-immutability",
    )


@pytest.mark.parametrize(
    "recovery_plan",
    ["", "   ", "none", "unrecoverable", "best effort only"],
)
def test_destructive_effect_with_empty_or_unrecoverable_plan_is_mandatory_deny(
    recovery_plan: str,
) -> None:
    _assert_mandatory_deny(
        _effect(
            operation="workspace.delete",
            final_arguments={"path": "obsolete.txt"},
            target="workspace/obsolete.txt",
            recovery_plan=recovery_plan,
        ),
        "IRREVERSIBLE_DESTRUCTION",
        "constraint.recoverable-effects",
    )


@pytest.mark.parametrize(
    "payload",
    [
        "api_key=must-not-leak",
        "Authorization: Bearer abcdefghijklmnop",
        "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----",
    ],
)
def test_egress_scans_every_nested_string_value_for_credentials(payload: str) -> None:
    _assert_mandatory_deny(
        _effect(
            operation="workspace.network.send",
            final_arguments={"envelope": {"items": [{"payload": payload}]}},
            target="workspace/network-request",
            recovery_plan="REMOVE_CREATED_PATH",
        ),
        "SECRET_EXFILTRATION",
        "constraint.no-secret-exfiltration",
    )


def test_benign_secretary_and_token_budget_strings_are_not_credentials() -> None:
    decision = _decision(
        _effect(
            operation="workspace.network.send",
            final_arguments={
                "payload": "secretary notes with token_budget=200",
                "destination": "https://example.invalid/upload",
            },
            target="workspace/network-request",
        )
    )

    assert decision.verdict is SafetyVerdict.APPROVAL_REQUIRED
    assert "SECRET_EXFILTRATION" not in (
        decision.guardian_decision.system_assessment.reason_codes
    )


@pytest.mark.parametrize(
    ("operation", "final_arguments"),
    [
        ("dataset.create_manifest", {"path": "dataset"}),
        ("gpu.launch_experiment", {"config": "dry-run"}),
        (
            "workspace.write",
            {
                "path": "report.md",
                "text": "content",
                "execution_route": "dataset_create_manifest",
            },
        ),
        (
            "workspace.write",
            {
                "path": "report.md",
                "text": "content",
                "direct_tool": "gpu_launch_experiment",
            },
        ),
    ],
)
def test_unknown_or_direct_legacy_tool_routes_are_mandatory_deny(
    operation: str, final_arguments: Dict[str, Any]
) -> None:
    _assert_mandatory_deny(
        _effect(operation=operation, final_arguments=final_arguments),
        "WORKSPACE_GATEWAY_BYPASS",
        "constraint.workspace-gateway-only",
    )
