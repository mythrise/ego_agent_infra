from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any, Dict, Mapping, Tuple

from benchmarks.secure_memory.canonical import (
    canonical_bytes,
    canonical_sha256,
    validate_guest_artifact_path,
)

from .contracts import CanonicalEffect, SafetyDecision, SafetyVerdict


WORKSPACE_MAPPING_VERSION = "agentteams-workspace-adapter/2026-09-01"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PROJECTION_FIELDS = (
    "source_effect_sha256",
    "safety_decision_sha256",
    "operation",
    "final_arguments",
    "target",
    "affected_scope",
    "project_id",
    "task_id",
    "workspace_checkpoint_sha256",
    "policy_sha256",
    "decision",
    "reversibility",
    "recovery",
)


def _validated_source_path(effect: CanonicalEffect) -> Tuple[str, str]:
    validate_guest_artifact_path(effect.target)
    target = PurePosixPath(effect.target)
    if len(target.parts) < 2 or target.parts[0] != "workspace":
        raise ValueError("source target must be a canonical workspace subpath")
    relative = PurePosixPath(*target.parts[1:]).as_posix()
    arguments = effect.final_arguments
    source_argument_path = arguments.get("path")
    if not isinstance(source_argument_path, str):
        raise ValueError("source arguments require one canonical path string")
    validate_guest_artifact_path(source_argument_path)
    if source_argument_path != relative:
        raise ValueError("source argument path does not match the exact target")
    return relative, f"{effect.project_id}/{relative}"


def _validate_source_authority(effect: CanonicalEffect) -> None:
    expected_scope = tuple(
        sorted((f"project:{effect.project_id}", f"task:{effect.task_id}"))
    )
    if effect.affected_scope != expected_scope:
        raise ValueError("source affected scope must exactly bind project and task")
    if _IDENTIFIER.fullmatch(effect.project_id) is None:
        raise ValueError("source project ID is not a workspace identifier")
    if _IDENTIFIER.fullmatch(effect.task_id) is None:
        raise ValueError("source task ID is not a workspace identifier")
    if effect.reversibility != "REVERSIBLE":
        raise ValueError("workspace mapping requires an explicitly reversible effect")


def _project_recovery(effect: CanonicalEffect) -> Dict[str, str | None]:
    plan = effect.recovery_plan
    if plan == "REMOVE_CREATED_PATH":
        recovery: Dict[str, str | None] = {
            "mode": "REMOVE_CREATED_PATH",
            "backup_path": None,
        }
    elif plan.startswith("RESTORE_BACKUP:"):
        source_backup = plan.removeprefix("RESTORE_BACKUP:")
        validate_guest_artifact_path(source_backup)
        parts = PurePosixPath(source_backup).parts
        if len(parts) < 3 or parts[:2] != ("workspace", ".recovery"):
            raise ValueError("recovery backup must be below workspace/.recovery")
        recovery = {
            "mode": "RESTORE_BACKUP",
            "backup_path": f"{effect.project_id}/{'/'.join(parts[1:])}",
        }
    else:
        raise ValueError("source recovery plan is not exactly mappable")

    if effect.operation == "workspace.mkdir" and recovery["mode"] != "REMOVE_CREATED_PATH":
        raise ValueError("workspace.mkdir requires REMOVE_CREATED_PATH recovery")
    if effect.operation == "workspace.delete" and recovery["mode"] != "RESTORE_BACKUP":
        raise ValueError("workspace.delete requires RESTORE_BACKUP recovery")
    return recovery


def _project_arguments(effect: CanonicalEffect) -> Tuple[str, Dict[str, Any]]:
    arguments: Mapping[str, Any] = effect.final_arguments
    if effect.operation == "workspace.write":
        if set(arguments) != {"path", "text"} or not isinstance(arguments["text"], str):
            raise ValueError("workspace.write requires only exact path and text arguments")
        return "WRITE_TEXT", {
            "operation": "WRITE_TEXT",
            "content_utf8": arguments["text"],
        }
    if effect.operation == "workspace.mkdir":
        if set(arguments) != {"path"}:
            raise ValueError("workspace.mkdir requires only the exact path argument")
        return "MAKE_DIRECTORY", {"operation": "MAKE_DIRECTORY"}
    if effect.operation == "workspace.delete":
        if set(arguments) != {"path"}:
            raise ValueError("workspace.delete requires only the exact path argument")
        return "DELETE_FILE", {"operation": "DELETE_FILE"}
    raise ValueError("source operation has no typed workspace mapping")


def _raw_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def build_workspace_effect(safety_decision: SafetyDecision) -> Dict[str, Any]:
    """Project one validated SafetyDecision into the strict Task 3B wire contract."""

    safety = SafetyDecision.model_validate(safety_decision.model_dump(mode="python"))
    if safety.verdict is SafetyVerdict.DENY:
        raise ValueError("DENY safety decisions can never become workspace effects")
    effect = safety.effect
    _validate_source_authority(effect)
    _relative, target = _validated_source_path(effect)
    operation, final_arguments = _project_arguments(effect)
    recovery = _project_recovery(effect)
    backup_path = recovery["backup_path"]
    affected_scope = [target] if backup_path is None else [target, backup_path]

    core: Dict[str, Any] = {
        "schema": "egoagentos.workspace-effect.v1",
        "operation": operation,
        "final_arguments": final_arguments,
        "target": target,
        "affected_scope": affected_scope,
        "project_id": effect.project_id,
        "task_id": effect.task_id,
        "workspace_checkpoint_sha256": effect.workspace_checkpoint_sha256,
        "policy_sha256": effect.policy_sha256,
        "decision": safety.verdict.value,
        "reversibility": "REVERSIBLE",
        "recovery": recovery,
        "source_effect_sha256": effect.effect_sha256,
        "safety_decision_sha256": safety.decision_sha256,
    }
    projection_core = {
        "mapping_version": WORKSPACE_MAPPING_VERSION,
        **{field: core[field] for field in _PROJECTION_FIELDS},
    }
    core["projection_sha256"] = canonical_sha256(
        "agentteams-workspace-wire-projection",
        projection_core,
    )
    return {**core, "effect_sha256": _raw_sha256(core)}


__all__ = ["WORKSPACE_MAPPING_VERSION", "build_workspace_effect"]
