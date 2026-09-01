from __future__ import annotations

import hashlib
from typing import Any

import pytest

from apps.agentteams_bridge.extensions.safety import evaluate_effect_safety
from apps.agentteams_bridge.extensions.workspace_adapter import build_workspace_effect
from apps.agentteams_bridge.extensions.workspace_authority import (
    ControlLedgerWorkspaceEffectVerifier,
)
from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256
from benchmarks.secure_memory.models import SignedTaskLease


SHA = "a" * 64
POLICY = "b" * 64


def _effect() -> Any:
    values = {
        "schema_version": "agentteams-canonical-effect/v1",
        "effect_id": "authority-effect",
        "operation": "workspace.write",
        "final_arguments": {"path": "report.md", "text": "accepted\n"},
        "target": "workspace/report.md",
        "affected_scope": ("project:project-1", "task:task-1"),
        "project_id": "project-1",
        "task_id": "task-1",
        "workspace_checkpoint_sha256": SHA,
        "policy_sha256": POLICY,
        "reversibility": "REVERSIBLE",
        "recovery_plan": "REMOVE_CREATED_PATH",
    }
    values["effect_sha256"] = canonical_sha256("agentteams-canonical-effect", values)
    from apps.agentteams_bridge.extensions.contracts import CanonicalEffect

    return CanonicalEffect.model_validate(values)


class _ReplayStore:
    def __init__(self, safety: Any) -> None:
        self.safety = safety
        core = {
            "schema_version": "secure-memory-task-lease/v1",
            "campaign_id": "campaign-1",
            "configuration_id": None,
            "execution_phase_owner": "QUALIFICATION",
            "problem_id": "__qualification__",
            "turn": 1,
            "generation": 1,
            "manifest_sha256": SHA,
            "post_selection_extension_sha256": None,
            "policy_sha256": POLICY,
            "requirement_ledger_sha256": SHA,
            "workspace_checkpoint_sha256": SHA,
            "memory_watermark": 0,
            "project_id": "project-1",
            "task_id": "task-1",
            "worker": "ego-runtime",
            "matrix_user_id": "@runtime:example.org",
            "role": "worker",
            "stage": "EXECUTE",
            "allowed_skills": (),
            "allowed_tools": (),
            "request_class": "main",
            "issued_ticket_ids": (),
            "expires_at_sequence": 10,
            "issuer_id": "control",
            "key_id": "control-key",
            "issue_sequence": 1,
        }
        core_sha = canonical_sha256("task-lease-core", core)
        self.lease = SignedTaskLease.model_validate(
            {"core": core, "core_sha256": core_sha, "signature_base64": "sig"}
        )

    def replay_extension_authority(self, run_id: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "campaign_binding": {
                "binding": {
                    "policy_sha256": POLICY,
                    "workspace_checkpoint_sha256": SHA,
                    "campaign_id": "campaign-1",
                }
            },
            "safety_decisions": [{"event": self.safety.model_dump(mode="json")}],
            "task_leases": [
                {
                    "task_id": "task-1",
                    "canonical_signed_payload": canonical_bytes(self.lease.model_dump(mode="json")),
                }
            ],
            "events": {"chain_valid": True},
        }


def test_verifier_requires_exact_control_admitted_safety_decision() -> None:
    safety = evaluate_effect_safety(_effect(), sequence=1, approval_expires_at_sequence=4)
    wire = build_workspace_effect(safety)
    verifier = ControlLedgerWorkspaceEffectVerifier(
        _ReplayStore(safety), run_id="run-1", project_id="project-1", configuration_id=None
    )

    verifier(wire)

    forged = dict(wire)
    forged["task_id"] = "task-forged"
    forged["effect_sha256"] = hashlib.sha256(
        canonical_bytes({key: value for key, value in forged.items() if key != "effect_sha256"})
    ).hexdigest()
    with pytest.raises(ValueError, match="does not match|admitted"):
        verifier(forged)


def test_verifier_rejects_self_consistent_effect_without_admitted_safety() -> None:
    safety = evaluate_effect_safety(_effect(), sequence=1, approval_expires_at_sequence=4)
    wire = build_workspace_effect(safety)

    class EmptyStore(_ReplayStore):
        def replay_extension_authority(self, run_id: str, **kwargs: Any) -> dict[str, Any]:
            value = super().replay_extension_authority(run_id, **kwargs)
            value["safety_decisions"] = []
            return value

    verifier = ControlLedgerWorkspaceEffectVerifier(
        EmptyStore(safety), run_id="run-1", project_id="project-1", configuration_id=None
    )
    with pytest.raises(ValueError, match="exactly one admitted"):
        verifier(wire)
