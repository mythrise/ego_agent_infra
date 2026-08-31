from __future__ import annotations

import importlib
import inspect
import sys
from typing import Any, Dict

import pytest

from apps.agentteams_bridge.extensions.contracts import CanonicalEffect, SafetyDecision
from apps.agentteams_bridge.extensions.safety import evaluate_effect_safety
from apps.agentteams_bridge.extensions.workspace_adapter import (
    WORKSPACE_MAPPING_VERSION,
    build_workspace_effect,
)
from benchmarks.secure_memory.canonical import canonical_sha256


SHA = "a" * 64
OTHER_SHA = "b" * 64


def _effect(**overrides: Any) -> CanonicalEffect:
    values: Dict[str, Any] = {
        "schema_version": "agentteams-canonical-effect/v1",
        "effect_id": "effect-adapter",
        "operation": "workspace.write",
        "final_arguments": {"path": "report.md", "text": "accepted evidence\n"},
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


def _safety(effect: CanonicalEffect) -> SafetyDecision:
    return evaluate_effect_safety(
        effect,
        sequence=7,
        approval_expires_at_sequence=11,
    )


def _projection_core(wire: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mapping_version": WORKSPACE_MAPPING_VERSION,
        "source_effect_sha256": wire["source_effect_sha256"],
        "safety_decision_sha256": wire["safety_decision_sha256"],
        "operation": wire["operation"],
        "final_arguments": wire["final_arguments"],
        "target": wire["target"],
        "affected_scope": wire["affected_scope"],
        "project_id": wire["project_id"],
        "task_id": wire["task_id"],
        "workspace_checkpoint_sha256": wire["workspace_checkpoint_sha256"],
        "policy_sha256": wire["policy_sha256"],
        "decision": wire["decision"],
        "reversibility": wire["reversibility"],
        "recovery": wire["recovery"],
    }


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        (
            _effect(),
            {
                "operation": "WRITE_TEXT",
                "final_arguments": {
                    "operation": "WRITE_TEXT",
                    "content_utf8": "accepted evidence\n",
                },
                "target": "project-1/report.md",
                "affected_scope": ["project-1/report.md"],
                "recovery": {"mode": "REMOVE_CREATED_PATH", "backup_path": None},
                "decision": "ALLOW",
            },
        ),
        (
            _effect(
                operation="workspace.mkdir",
                final_arguments={"path": "generated"},
                target="workspace/generated",
            ),
            {
                "operation": "MAKE_DIRECTORY",
                "final_arguments": {"operation": "MAKE_DIRECTORY"},
                "target": "project-1/generated",
                "affected_scope": ["project-1/generated"],
                "recovery": {"mode": "REMOVE_CREATED_PATH", "backup_path": None},
                "decision": "ALLOW",
            },
        ),
        (
            _effect(
                operation="workspace.delete",
                final_arguments={"path": "obsolete.txt"},
                target="workspace/obsolete.txt",
                recovery_plan=(
                    "RESTORE_BACKUP:workspace/.recovery/obsolete.txt.effect-adapter.bak"
                ),
            ),
            {
                "operation": "DELETE_FILE",
                "final_arguments": {"operation": "DELETE_FILE"},
                "target": "project-1/obsolete.txt",
                "affected_scope": [
                    "project-1/obsolete.txt",
                    "project-1/.recovery/obsolete.txt.effect-adapter.bak",
                ],
                "recovery": {
                    "mode": "RESTORE_BACKUP",
                    "backup_path": (
                        "project-1/.recovery/obsolete.txt.effect-adapter.bak"
                    ),
                },
                "decision": "APPROVAL_REQUIRED",
            },
        ),
    ],
)
def test_adapter_projects_each_allowed_operation_and_binds_both_authorities(
    effect: CanonicalEffect, expected: Dict[str, Any]
) -> None:
    safety = _safety(effect)

    wire = build_workspace_effect(safety)

    for key, value in expected.items():
        assert wire[key] == value
    assert wire["schema"] == "egoagentos.workspace-effect.v1"
    assert wire["project_id"] == effect.project_id
    assert wire["task_id"] == effect.task_id
    assert wire["workspace_checkpoint_sha256"] == effect.workspace_checkpoint_sha256
    assert wire["policy_sha256"] == effect.policy_sha256
    assert wire["source_effect_sha256"] == effect.effect_sha256
    assert wire["safety_decision_sha256"] == safety.decision_sha256
    assert wire["projection_sha256"] == canonical_sha256(
        "agentteams-workspace-wire-projection",
        _projection_core(wire),
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Task 3B requires Python 3.12")
def test_adapter_output_parses_as_exact_task3b_contract_with_matching_digests() -> None:
    contract = importlib.import_module("egoagentos_mcp.workspace_contract")
    wire = build_workspace_effect(_safety(_effect()))

    parsed = contract.WorkspaceEffect.model_validate(wire)

    assert parsed.source_effect_sha256 == wire["source_effect_sha256"]
    assert parsed.safety_decision_sha256 == wire["safety_decision_sha256"]
    assert parsed.projection_sha256 == wire["projection_sha256"]
    assert contract.workspace_projection_sha256(parsed) == wire["projection_sha256"]
    assert contract.workspace_effect_sha256(parsed) == wire["effect_sha256"]


@pytest.mark.parametrize(
    "effect",
    [
        _effect(
            final_arguments={"path": "other.md", "text": "accepted evidence\n"},
        ),
        _effect(
            final_arguments={
                "path": "report.md",
                "text": "accepted evidence\n",
                "extra": "unreviewed",
            },
        ),
        _effect(
            operation="workspace.mkdir",
            final_arguments={"path": "generated"},
            target="workspace/generated",
            recovery_plan=("RESTORE_BACKUP:workspace/.recovery/generated.bak"),
        ),
        _effect(
            operation="workspace.delete",
            final_arguments={"path": "obsolete.txt"},
            target="workspace/obsolete.txt",
            recovery_plan="REMOVE_CREATED_PATH",
        ),
        _effect(operation="workspace.copy"),
    ],
)
def test_adapter_rejects_unknown_mapping_or_source_argument_target_recovery_drift(
    effect: CanonicalEffect,
) -> None:
    with pytest.raises(ValueError):
        build_workspace_effect(_safety(effect))


def test_adapter_revalidates_the_complete_safety_decision_and_never_accepts_deny() -> None:
    denied = _safety(
        _effect(
            operation="dataset.create_manifest",
            final_arguments={"path": "dataset"},
        )
    )
    assert denied.verdict.value == "DENY"
    with pytest.raises(ValueError, match="DENY"):
        build_workspace_effect(denied)

    valid = _safety(_effect())
    forged = valid.model_copy(
        update={
            "effect": valid.effect.model_copy(update={"task_id": "task-forged"})
        }
    )
    with pytest.raises(ValueError):
        build_workspace_effect(forged)


def test_projection_is_internal_and_changes_with_every_projected_authority_field() -> None:
    first = build_workspace_effect(_safety(_effect()))
    changed = build_workspace_effect(
        _safety(
            _effect(
                effect_id="effect-adapter-2",
                task_id="task-2",
                affected_scope=("project:project-1", "task:task-2"),
            )
        )
    )

    assert "projection_sha256" not in inspect.signature(build_workspace_effect).parameters
    assert changed["projection_sha256"] != first["projection_sha256"]
    assert changed["effect_sha256"] != first["effect_sha256"]
