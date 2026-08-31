"""Strict canonical contracts for the typed workspace effect gateway."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal, Mapping, Union

from pydantic import ConfigDict, Field, model_validator

from .common import StrictModel, canonical_json, canonical_sha256

SHA256_PATTERN = r"^[0-9a-f]{64}$"
IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
MAX_WRITE_TEXT_BYTES = 1024 * 1024
WORKSPACE_MAPPING_VERSION = "agentteams-workspace-adapter/2026-09-01"
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


class _WorkspaceModel(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
        serialize_by_alias=True,
    )


class WriteTextArguments(_WorkspaceModel):
    operation: Literal["WRITE_TEXT"]
    content_utf8: str = Field(max_length=MAX_WRITE_TEXT_BYTES, repr=False)


class MakeDirectoryArguments(_WorkspaceModel):
    operation: Literal["MAKE_DIRECTORY"]


class DeleteFileArguments(_WorkspaceModel):
    operation: Literal["DELETE_FILE"]


WorkspaceArguments = Annotated[
    Union[WriteTextArguments, MakeDirectoryArguments, DeleteFileArguments],
    Field(discriminator="operation"),
]


class RecoveryPlan(_WorkspaceModel):
    mode: Literal["REMOVE_CREATED_PATH", "RESTORE_BACKUP"]
    backup_path: str | None


class WorkspaceEffectCore(_WorkspaceModel):
    contract_schema: Literal["egoagentos.workspace-effect.v1"] = Field(alias="schema")
    operation: Literal["WRITE_TEXT", "MAKE_DIRECTORY", "DELETE_FILE"]
    final_arguments: WorkspaceArguments
    target: str = Field(min_length=1, max_length=1024)
    affected_scope: list[str] = Field(min_length=1, max_length=2)
    project_id: str = Field(pattern=IDENTIFIER_PATTERN)
    task_id: str = Field(pattern=IDENTIFIER_PATTERN)
    workspace_checkpoint_sha256: str = Field(pattern=SHA256_PATTERN)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    decision: Literal["ALLOW", "APPROVAL_REQUIRED", "DENY"]
    reversibility: Literal["REVERSIBLE"]
    recovery: RecoveryPlan
    source_effect_sha256: str = Field(pattern=SHA256_PATTERN)
    safety_decision_sha256: str = Field(pattern=SHA256_PATTERN)
    projection_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_exact_operation_binding(self) -> WorkspaceEffectCore:
        if self.final_arguments.operation != self.operation:
            raise ValueError("operation must exactly match final_arguments.operation")
        expected_scope = [self.target]
        if self.recovery.backup_path is not None:
            expected_scope.append(self.recovery.backup_path)
        if self.affected_scope != expected_scope:
            raise ValueError("affected_scope must exactly enumerate the durable paths")
        if self.operation == "MAKE_DIRECTORY" and (
            self.recovery.mode != "REMOVE_CREATED_PATH"
            or self.recovery.backup_path is not None
        ):
            raise ValueError("MAKE_DIRECTORY recovery must remove the created path")
        if self.operation == "DELETE_FILE" and (
            self.recovery.mode != "RESTORE_BACKUP"
            or self.recovery.backup_path is None
        ):
            raise ValueError("DELETE_FILE requires an exact recoverable backup path")
        if self.recovery.mode == "RESTORE_BACKUP" and self.recovery.backup_path is None:
            raise ValueError("RESTORE_BACKUP requires backup_path")
        if self.recovery.mode == "REMOVE_CREATED_PATH" and self.recovery.backup_path is not None:
            raise ValueError("REMOVE_CREATED_PATH cannot bind a backup_path")
        if workspace_projection_sha256(self) != self.projection_sha256:
            raise ValueError("projection digest does not match the exact mapped authority")
        return self


class WorkspaceEffect(WorkspaceEffectCore):
    effect_sha256: str = Field(pattern=SHA256_PATTERN)


def _effect_core(value: WorkspaceEffectCore | Mapping[str, Any]) -> WorkspaceEffectCore:
    if isinstance(value, WorkspaceEffectCore):
        payload = value.model_dump(
            mode="json", by_alias=True, exclude={"effect_sha256"}
        )
    else:
        payload = {key: item for key, item in value.items() if key != "effect_sha256"}
    return WorkspaceEffectCore.model_validate(payload)


def workspace_effect_sha256(value: WorkspaceEffectCore | Mapping[str, Any]) -> str:
    """Hash every authority-bearing effect field except the digest itself."""

    return canonical_sha256(_effect_core(value).model_dump(mode="json", by_alias=True))


def workspace_projection_sha256(value: WorkspaceEffectCore | Mapping[str, Any]) -> str:
    """Bind the fixed adapter mapping, source Safety authority, and projected fields."""

    if isinstance(value, WorkspaceEffectCore):
        payload = value.model_dump(mode="json", by_alias=True)
    else:
        payload = dict(value)
    projection = {
        "mapping_version": WORKSPACE_MAPPING_VERSION,
        **{field: payload[field] for field in _PROJECTION_FIELDS},
    }
    prefix = b"egoagentos:agentteams-workspace-wire-projection:v1\x00"
    return hashlib.sha256(prefix + canonical_json(projection).encode("utf-8")).hexdigest()


def workspace_receipt_sha256(value: Mapping[str, Any]) -> str:
    """Hash a content-free execution receipt, excluding its digest field."""

    return canonical_sha256(
        {key: item for key, item in value.items() if key != "receipt_sha256"}
    )
