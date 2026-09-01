from __future__ import annotations

from typing import Any

import pytest

from apps.agentteams_bridge.extensions import CanonicalEffect, SafetyVerdict
from apps.agentteams_bridge.extensions.guardian import EgoGuardian, mandatory_rule_matches
from apps.agentteams_bridge.extensions.safety import SystemRiskClassifier, evaluate_effect_safety
from apps.agentteams_bridge.extensions.workspace_adapter import build_workspace_effect
from benchmarks.secure_memory.canonical import canonical_sha256


SHA_A = "a" * 64
SHA_B = "b" * 64


def _private_key_canary() -> str:
    """Construct a detector canary without storing key-shaped text in source."""

    return "".join(
        (
            "-----BEGIN ",
            "PRIVATE KEY-----\n",
            "not-a-real-key\n",
            "-----END ",
            "PRIVATE KEY-----",
        )
    )


def _effect(operation: str, target: str, **overrides: Any) -> CanonicalEffect:
    values: dict[str, Any] = {
        "schema_version": "agentteams-canonical-effect/v1",
        "effect_id": "effect-fail-closed",
        "operation": operation,
        "final_arguments": {"path": target.removeprefix("workspace/")},
        "target": target,
        "affected_scope": ("project:project-a", "task:task-a"),
        "project_id": "project-a",
        "task_id": "task-a",
        "workspace_checkpoint_sha256": SHA_A,
        "policy_sha256": SHA_B,
        "reversibility": "REVERSIBLE",
        "recovery_plan": "restore the bound workspace checkpoint",
    }
    values.update(overrides)
    values["effect_sha256"] = canonical_sha256(
        "agentteams-canonical-effect",
        values,
    )
    return CanonicalEffect.model_validate(values)


@pytest.mark.parametrize(
    ("effect", "expected_reason"),
    [
        (
            _effect(
                "workspace.write",
                "workspace/report.md",
                affected_scope=("project:project-other", "task:task-a"),
            ),
            "CROSS_PROJECT_TARGET",
        ),
        (
            _effect(
                "workspace.write",
                "../outside.txt",
                final_arguments={"path": "../outside.txt"},
            ),
            "PATH_ESCAPE",
        ),
        (
            _effect(
                "workspace.upload",
                "workspace/export.json",
                final_arguments={"payload": {"api_key": "must-not-leak"}},
            ),
            "SECRET_EXFILTRATION",
        ),
        (
            _effect(
                "workspace.network.send",
                "workspace/request.json",
                final_arguments={
                    "body": "api_key=must-not-leak",
                    "path": "request.json",
                },
            ),
            "SECRET_EXFILTRATION",
        ),
        (
            _effect(
                "workspace.network.send",
                "workspace/request.json",
                final_arguments={
                    "body": _private_key_canary(),
                    "path": "request.json",
                },
            ),
            "SECRET_EXFILTRATION",
        ),
        (
            _effect(
                "workspace.delete",
                "workspace/results.bin",
                reversibility="IRREVERSIBLE",
                recovery_plan="none",
            ),
            "IRREVERSIBLE_DESTRUCTION",
        ),
        (
            _effect(
                "workspace.write",
                "workspace/evidence/result.json",
            ),
            "EVIDENCE_TAMPERING",
        ),
        (
            _effect(
                "direct.shell",
                "workspace/run.sh",
            ),
            "WORKSPACE_GATEWAY_BYPASS",
        ),
        (
            _effect(
                "workspace.write",
                "workspace/report.md",
                final_arguments={"gateway": "DIRECT", "path": "report.md"},
            ),
            "WORKSPACE_GATEWAY_BYPASS",
        ),
    ],
)
def test_mandatory_rules_fail_closed_for_boundary_bypasses(
    effect: CanonicalEffect,
    expected_reason: str,
) -> None:
    matches = dict(mandatory_rule_matches(effect))
    assert expected_reason in matches

    system = SystemRiskClassifier.assess(effect, sequence=20)
    assert system.risk_level.value == "HIGH"
    assert system.disposition.value == "DENY"
    assert expected_reason in system.reason_codes
    assert matches[expected_reason] in system.mandatory_constraint_ids

    decision = evaluate_effect_safety(
        effect,
        sequence=20,
        approval_expires_at_sequence=30,
    )
    assert decision.verdict is SafetyVerdict.DENY
    assert decision.approval_pending is False
    assert decision.approval_disclosure is None
    assert decision.guardian_decision.guardian_assessment is not None
    assert expected_reason in decision.guardian_decision.guardian_assessment.reason_codes


def test_unknown_operations_are_denied_by_both_system_and_guardian() -> None:
    effect = _effect("workspace.future_unregistered_effect", "workspace/future.bin")

    system = SystemRiskClassifier.assess(effect, sequence=40)
    assert system.risk_level.value == "HIGH"
    assert system.disposition.value == "DENY"
    assert system.reason_codes == ("UNKNOWN_OPERATION",)
    assert system.mandatory_constraint_ids == ("constraint.known-operation",)

    guardian = EgoGuardian.assess(effect, system, sequence=41)
    assert guardian.risk_level.value == "HIGH"
    assert guardian.disposition.value == "DENY"
    assert "UNKNOWN_OPERATION" in guardian.reason_codes
    assert "constraint.known-operation" in guardian.mandatory_constraint_ids


def test_cross_project_scope_cannot_hide_behind_additional_scope_labels() -> None:
    effect = _effect(
        "workspace.write",
        "workspace/report.md",
        affected_scope=(
            "project:project-a",
            "project:project-b",
            "task:task-a",
        ),
    )

    decision = evaluate_effect_safety(
        effect,
        sequence=50,
        approval_expires_at_sequence=60,
    )

    assert decision.verdict is SafetyVerdict.DENY
    assert "CROSS_PROJECT_TARGET" in decision.guardian_decision.system_assessment.reason_codes


def test_workspace_projection_is_unavailable_for_denied_effects() -> None:
    effect = _effect(
        "workspace.write",
        "../escape.txt",
        final_arguments={"path": "../escape.txt"},
    )
    decision = evaluate_effect_safety(
        effect,
        sequence=70,
        approval_expires_at_sequence=80,
    )

    assert decision.verdict is SafetyVerdict.DENY
    with pytest.raises(ValueError, match="DENY|ALLOW|approved"):
        build_workspace_effect(decision)


def test_unknown_operation_cannot_become_an_approval_request() -> None:
    effect = _effect("workspace.future_unregistered_effect", "workspace/future.bin")

    decision = evaluate_effect_safety(
        effect,
        sequence=90,
        approval_expires_at_sequence=100,
    )

    assert decision.verdict is SafetyVerdict.DENY
    assert decision.approval_pending is False
    assert decision.approval_disclosure is None


@pytest.mark.parametrize(
    "target",
    [
        "workspace//report.md",
        "workspace/report.md/",
        "workspace/./report.md",
        "workspace/reports/../report.md",
        "workspace\\report.md",
        "workspace/report.md\x00suffix",
        "C:/workspace/report.md",
    ],
)
def test_noncanonical_workspace_targets_are_denied(target: str) -> None:
    effect = _effect(
        "workspace.write",
        target,
        final_arguments={"path": target},
    )

    decision = evaluate_effect_safety(
        effect,
        sequence=110,
        approval_expires_at_sequence=120,
    )

    assert decision.verdict is SafetyVerdict.DENY
    assert "PATH_ESCAPE" in decision.guardian_decision.system_assessment.reason_codes


@pytest.mark.parametrize(
    "recovery_plan",
    [
        "restore if needed",
        "we can probably reconstruct the file",
        "RESTORE_BACKUP:../backup.bin",
        "RESTORE_BACKUP:/tmp/backup.bin",
    ],
)
def test_destructive_effects_require_a_machine_checkable_recovery_plan(
    recovery_plan: str,
) -> None:
    effect = _effect(
        "workspace.delete",
        "workspace/results.bin",
        recovery_plan=recovery_plan,
    )

    decision = evaluate_effect_safety(
        effect,
        sequence=130,
        approval_expires_at_sequence=140,
    )

    assert decision.verdict is SafetyVerdict.DENY
    assert "IRREVERSIBLE_DESTRUCTION" in (
        decision.guardian_decision.system_assessment.reason_codes
    )


def test_nested_absolute_path_argument_is_denied() -> None:
    effect = _effect(
        "workspace.write",
        "workspace/report.md",
        final_arguments={
            "path": "report.md",
            "options": {"working_directory": "/tmp/outside"},
        },
    )

    decision = evaluate_effect_safety(
        effect,
        sequence=150,
        approval_expires_at_sequence=160,
    )

    assert decision.verdict is SafetyVerdict.DENY
    assert "PATH_ESCAPE" in decision.guardian_decision.system_assessment.reason_codes
