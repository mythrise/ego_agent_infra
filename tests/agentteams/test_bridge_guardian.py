from __future__ import annotations

from typing import Any, Dict

import pytest

from apps.agentteams_bridge.extensions.contracts import (
    CanonicalEffect,
    EnforcementMode,
    RiskDisposition,
    RiskLevel,
)
from apps.agentteams_bridge.extensions.guardian import EgoGuardian, build_guardian_decision
from apps.agentteams_bridge.extensions.safety import SystemRiskClassifier
from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256


SHA = "a" * 64
OTHER_SHA = "b" * 64


def _effect(**overrides: Any) -> CanonicalEffect:
    values: Dict[str, Any] = {
        "schema_version": "agentteams-canonical-effect/v1",
        "effect_id": "effect-guardian",
        "operation": "workspace.write",
        "final_arguments": {"path": "report.md", "text": "accepted evidence"},
        "target": "workspace/report.md",
        "affected_scope": ("project:project-1", "task:task-1"),
        "project_id": "project-1",
        "task_id": "task-1",
        "workspace_checkpoint_sha256": SHA,
        "policy_sha256": OTHER_SHA,
        "reversibility": "REVERSIBLE",
        "recovery_plan": "restore the bound workspace checkpoint",
    }
    values.update(overrides)
    values["effect_sha256"] = canonical_sha256("agentteams-canonical-effect", values)
    return CanonicalEffect.model_validate(values)


@pytest.mark.parametrize("operation", ["workspace.read", "workspace.write"])
def test_non_high_system_assessment_never_invokes_or_forges_guardian(operation: str) -> None:
    effect = _effect(operation=operation)
    system = SystemRiskClassifier.assess(effect, sequence=7)

    decision = build_guardian_decision(
        effect,
        system,
        enforcement_mode=EnforcementMode.ENFORCING,
        guardian_sequence=8,
    )

    assert system.risk_level is not RiskLevel.HIGH
    assert decision.guardian_assessment is None
    assert decision.disposition is RiskDisposition.ALLOW
    with pytest.raises(ValueError, match="HIGH"):
        EgoGuardian.assess(effect, system, sequence=8)


def test_high_effect_runs_independent_guardian_rules() -> None:
    effect = _effect(operation="workspace.delete")
    system = SystemRiskClassifier.assess(effect, sequence=7)

    decision = build_guardian_decision(
        effect,
        system,
        enforcement_mode=EnforcementMode.ENFORCING,
        guardian_sequence=8,
    )

    guardian = decision.guardian_assessment
    assert system.risk_level is RiskLevel.HIGH
    assert guardian is not None
    assert guardian.risk_level is RiskLevel.HIGH
    assert guardian.disposition is RiskDisposition.APPROVAL_REQUIRED
    assert guardian.rule_version == EgoGuardian.RULE_VERSION
    assert guardian.rule_sha256 == EgoGuardian.RULE_SHA256
    assert guardian.rule_sha256 != system.rule_sha256


@pytest.mark.parametrize(
    "overrides",
    [
        {"final_arguments": {"path": "../../host"}},
        {
            "operation": "workspace.network.send",
            "final_arguments": {"data_classification": "SECRET"},
        },
        {"operation": "workspace.delete", "reversibility": "IRREVERSIBLE"},
        {"target": "workspace/evidence/decision.json"},
        {"operation": "agentteams.shell"},
    ],
)
def test_guardian_cannot_downgrade_mandatory_system_constraints(overrides: Dict[str, Any]) -> None:
    effect = _effect(**overrides)
    system = SystemRiskClassifier.assess(effect, sequence=7)

    decision = build_guardian_decision(
        effect,
        system,
        enforcement_mode=EnforcementMode.ENFORCING,
        guardian_sequence=8,
    )

    guardian = decision.guardian_assessment
    assert system.risk_level is RiskLevel.HIGH
    assert system.disposition is RiskDisposition.DENY
    assert system.mandatory_constraint_ids
    assert guardian is not None
    assert guardian.risk_level is RiskLevel.HIGH
    assert guardian.disposition is RiskDisposition.DENY
    assert set(system.mandatory_constraint_ids).issubset(guardian.mandatory_constraint_ids)
    assert decision.disposition is RiskDisposition.DENY


def test_guardian_rejects_a_system_assessment_for_different_effect_bytes() -> None:
    original = _effect(operation="workspace.delete")
    changed = _effect(
        operation="workspace.delete",
        final_arguments={"path": "different.md"},
    )
    system = SystemRiskClassifier.assess(original, sequence=7)

    with pytest.raises(ValueError, match="effect"):
        build_guardian_decision(
            changed,
            system,
            enforcement_mode=EnforcementMode.ENFORCING,
            guardian_sequence=8,
        )


def test_guardian_replay_is_byte_identical() -> None:
    effect = _effect(operation="workspace.delete")
    system = SystemRiskClassifier.assess(effect, sequence=7)

    first = build_guardian_decision(
        effect,
        system,
        enforcement_mode=EnforcementMode.ENFORCING,
        guardian_sequence=8,
    )
    second = build_guardian_decision(
        effect,
        system,
        enforcement_mode=EnforcementMode.ENFORCING,
        guardian_sequence=8,
    )

    assert first == second
    assert canonical_bytes(first) == canonical_bytes(second)
    assert first.decision_sha256 == second.decision_sha256
