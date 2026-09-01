"""Control-ledger authority verification for the typed workspace gateway.

The MCP process must not trust a caller supplied ``WorkspaceEffect`` merely
because its local digests are internally consistent.  This verifier replays
the immutable AgentTeams extension stream and compares the wire payload with
the exact SafetyDecision admitted by Control.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Optional

from benchmarks.secure_memory.canonical import canonical_bytes, parse_json_bytes
from benchmarks.secure_memory.models import SignedTaskLease

from .contracts import SafetyDecision
from .workspace_adapter import build_workspace_effect

if TYPE_CHECKING:
    from ..store import BridgeStoreContract


def _wire_payload(effect: Any) -> dict[str, Any]:
    if hasattr(effect, "model_dump"):
        value = effect.model_dump(mode="json", by_alias=True)
    elif isinstance(effect, Mapping):
        value = dict(effect)
    else:
        raise TypeError("workspace effect must be a typed model or mapping")
    if not isinstance(value, dict):
        raise TypeError("workspace effect must serialize to an object")
    return value


def _wire_effect_digest(value: Mapping[str, Any]) -> str:
    core = {key: item for key, item in value.items() if key != "effect_sha256"}
    return hashlib.sha256(canonical_bytes(core)).hexdigest()


class ControlLedgerWorkspaceEffectVerifier:
    """Verify one workspace effect against an immutable bridge authority stream.

    ``replay_extension_authority`` is intentionally the only persistence API
    used.  Both SQLite and PostgreSQL implementations therefore share exactly
    the same fail-closed verification path.
    """

    def __init__(
        self,
        store: BridgeStoreContract,
        *,
        run_id: str,
        project_id: str,
        configuration_id: Optional[str],
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.project_id = project_id
        self.configuration_id = configuration_id

    def __call__(self, effect: Any) -> None:
        wire = _wire_payload(effect)
        expected_wire_digest = _wire_effect_digest(wire)
        if wire.get("effect_sha256") != expected_wire_digest:
            raise ValueError("workspace wire effect digest is not canonical")

        replay = self.store.replay_extension_authority(
            self.run_id,
            project_id=self.project_id,
            configuration_id=self.configuration_id,
        )
        if not replay["events"]["chain_valid"]:
            raise ValueError("Control extension event chain is invalid")

        safety_events = replay.get("safety_decisions", [])
        matched: list[SafetyDecision] = []
        for item in safety_events:
            payload = item.get("event")
            if not isinstance(payload, Mapping):
                continue
            try:
                safety = SafetyDecision.model_validate(payload)
            except (TypeError, ValueError):
                continue
            if safety.decision_sha256 == wire.get("safety_decision_sha256"):
                matched.append(safety)
        if len(matched) != 1:
            raise ValueError("workspace effect is not bound to exactly one admitted SafetyDecision")

        safety = matched[0]
        campaign = replay["campaign_binding"]["binding"]
        source = safety.effect
        if wire.get("project_id") != self.project_id:
            raise ValueError("workspace effect project is outside the Control run")
        if source.project_id != self.project_id:
            raise ValueError("admitted source effect project is outside the Control run")
        if source.policy_sha256 != campaign["policy_sha256"]:
            raise ValueError("source effect policy is not bound to the campaign")
        if source.workspace_checkpoint_sha256 != campaign["workspace_checkpoint_sha256"]:
            raise ValueError("source effect checkpoint is not bound to the campaign")

        leases = []
        for item in replay.get("task_leases", []):
            if item.get("task_id") != source.task_id:
                continue
            try:
                leases.append(
                    SignedTaskLease.model_validate(
                        parse_json_bytes(item["canonical_signed_payload"])
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        if len(leases) != 1:
            raise ValueError("workspace effect requires exactly one admitted task lease")
        lease = leases[0].core
        if (
            lease.project_id != self.project_id
            or lease.task_id != source.task_id
            or lease.policy_sha256 != campaign["policy_sha256"]
            or lease.workspace_checkpoint_sha256 != campaign["workspace_checkpoint_sha256"]
            or lease.campaign_id != campaign["campaign_id"]
        ):
            raise ValueError("task lease is not bound to the campaign and workspace effect")

        expected = build_workspace_effect(safety)
        if canonical_bytes(expected) != canonical_bytes(wire):
            raise ValueError("workspace wire effect does not match the admitted SafetyDecision")


__all__ = ["ControlLedgerWorkspaceEffectVerifier"]
