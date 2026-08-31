"""Descriptor-oriented, fail-closed execution for canonical workspace effects."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

from .common import (
    StructuredToolError,
    TrustedRoot,
    canonical_sha256,
    descriptor_sha256,
    is_sensitive_path,
    open_child_descriptor,
    redact_text,
)
from .workspace_contract import (
    DeleteFileArguments,
    MakeDirectoryArguments,
    MAX_WRITE_TEXT_BYTES,
    WorkspaceEffect,
    WriteTextArguments,
    workspace_effect_sha256,
    workspace_receipt_sha256,
)


class ApprovalVerifier(Protocol):
    """Injected exact-receipt verifier; implementations own expiry and replay state."""

    def __call__(self, approval_receipt: str, effect: WorkspaceEffect) -> None: ...


def _validate_project_id(root: TrustedRoot, project_id: str) -> str:
    normalised = root.normalised_relative(project_id)
    if normalised != project_id or len(PurePosixPath(normalised).parts) != 1:
        raise StructuredToolError(
            "project_scope_mismatch", "Project IDs must name one canonical workspace directory"
        )
    if is_sensitive_path(project_id):
        raise StructuredToolError("sensitive_path_rejected", "Sensitive project paths are forbidden")
    return normalised


def _checkpoint_entries(directory_descriptor: int, base: str) -> list[dict[str, Any]]:
    try:
        with os.scandir(directory_descriptor) as scanner:
            entries = sorted(scanner, key=lambda item: item.name)
    except OSError as exc:
        raise StructuredToolError(
            "workspace_checkpoint_failed",
            "The workspace could not be inspected safely",
            {"reason": type(exc).__name__},
        ) from exc

    result: list[dict[str, Any]] = []
    for entry in entries:
        relative = "%s/%s" % (base, entry.name)
        if entry.is_symlink():
            raise StructuredToolError("symlink_rejected", "Workspace symlinks are forbidden")
        if is_sensitive_path(relative):
            raise StructuredToolError(
                "sensitive_path_rejected", "Sensitive workspace paths are forbidden"
            )
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise StructuredToolError(
                "filesystem_race_detected",
                "A workspace entry changed during checkpointing",
                {"reason": type(exc).__name__},
            ) from exc
        if stat.S_ISDIR(metadata.st_mode):
            child, _opened = open_child_descriptor(
                directory_descriptor,
                entry.name,
                expected=metadata,
                require_directory=True,
            )
            try:
                result.append({"path": relative, "kind": "directory"})
                result.extend(_checkpoint_entries(child, relative))
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            child, opened = open_child_descriptor(
                directory_descriptor,
                entry.name,
                expected=metadata,
                require_file=True,
            )
            try:
                result.append(
                    {
                        "path": relative,
                        "kind": "file",
                        "size_bytes": opened.st_size,
                        "sha256": descriptor_sha256(child),
                    }
                )
            finally:
                os.close(child)
        else:
            raise StructuredToolError(
                "special_file_rejected", "Special files are forbidden in managed workspaces"
            )
    return result


def workspace_checkpoint_sha256(root: str | Path, project_id: str) -> str:
    """Return a deterministic digest of every directory and regular file in a project."""

    trusted_root = TrustedRoot(root, label="workspace root")
    project = _validate_project_id(trusted_root, project_id)
    with trusted_root.open_directory_descriptor(project) as (descriptor, normalised):
        entries = _checkpoint_entries(descriptor, normalised)
    return canonical_sha256(
        {
            "schema": "egoagentos.workspace-checkpoint.v1",
            "project_id": project,
            "entries": entries,
        }
    )


def _parent_and_name(path: str) -> tuple[str, str]:
    pure = PurePosixPath(path)
    if not pure.parts or pure.name in {"", ".", ".."}:
        raise StructuredToolError("invalid_path", "A concrete workspace target is required")
    return (pure.parent.as_posix(), pure.name)


def _lstat_child(parent_descriptor: int, name: str) -> os.stat_result | None:
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StructuredToolError(
            "filesystem_race_detected",
            "The workspace target changed while it was inspected",
            {"reason": type(exc).__name__},
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise StructuredToolError("symlink_rejected", "Workspace symlinks are forbidden")
    return metadata


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "short workspace write")
        view = view[written:]


def _atomic_write_bytes(parent_descriptor: int, name: str, data: bytes) -> None:
    temporary = ".egoagentos-tmp-%s" % uuid.uuid4().hex
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_descriptor)
        _write_all(descriptor, data)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary,
            name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_descriptor)
        except OSError:
            pass
        raise StructuredToolError(
            "atomic_write_failed",
            "The workspace file could not be replaced atomically",
            {"reason": type(exc).__name__},
        ) from exc


class WorkspaceExecutor:
    """The sole filesystem mutation authority for canonical workspace effects."""

    def __init__(
        self,
        root: str | Path,
        *,
        approval_verifier: ApprovalVerifier | Callable[[str, WorkspaceEffect], None] | None = None,
    ) -> None:
        self.root = TrustedRoot(root, label="workspace root")
        self.approval_verifier = approval_verifier

    @classmethod
    def from_env(
        cls,
        *,
        approval_verifier: ApprovalVerifier
        | Callable[[str, WorkspaceEffect], None]
        | None = None,
    ) -> WorkspaceExecutor:
        root = TrustedRoot.from_env("EGO_MCP_WORKSPACE_ROOT", label="workspace root")
        return cls(root.path, approval_verifier=approval_verifier)

    def _normalise_bound_path(self, path: str, project_id: str) -> str:
        normalised = self.root.normalised_relative(path)
        if normalised != path:
            raise StructuredToolError("invalid_path", "Workspace paths must be canonical")
        parts = PurePosixPath(normalised).parts
        if not parts or parts[0] != project_id:
            raise StructuredToolError(
                "project_scope_mismatch", "The effect target is outside its exact project"
            )
        if is_sensitive_path(normalised):
            raise StructuredToolError(
                "sensitive_path_rejected", "Sensitive workspace paths cannot be mutated"
            )
        return normalised

    def _validate_authority(self, effect: WorkspaceEffect) -> tuple[str, str | None]:
        actual_effect_sha256 = workspace_effect_sha256(effect)
        if actual_effect_sha256 != effect.effect_sha256:
            raise StructuredToolError(
                "effect_digest_mismatch", "The canonical effect fields changed after admission"
            )
        project = _validate_project_id(self.root, effect.project_id)
        target = self._normalise_bound_path(effect.target, project)
        backup = effect.recovery.backup_path
        if backup is not None:
            backup = self._normalise_bound_path(backup, project)
            if backup == target:
                raise StructuredToolError(
                    "recovery_path_invalid", "Recovery backup must differ from the target"
                )
        if effect.affected_scope != ([target] if backup is None else [target, backup]):
            raise StructuredToolError(
                "affected_scope_mismatch", "The effect affected scope is not exact"
            )
        arguments = effect.final_arguments
        if isinstance(arguments, WriteTextArguments):
            if "\x00" in arguments.content_utf8:
                raise StructuredToolError("invalid_text", "WRITE_TEXT rejects NUL content")
            if redact_text(arguments.content_utf8) != arguments.content_utf8:
                raise StructuredToolError(
                    "secret_content_rejected", "Credential-like content cannot be written"
                )
        return target, backup

    def _verify_checkpoint(self, effect: WorkspaceEffect) -> str:
        actual = workspace_checkpoint_sha256(self.root.path, effect.project_id)
        if actual != effect.workspace_checkpoint_sha256:
            raise StructuredToolError(
                "workspace_checkpoint_stale",
                "The workspace changed after the effect was admitted",
                {
                    "expected_checkpoint_sha256": effect.workspace_checkpoint_sha256,
                    "actual_checkpoint_sha256": actual,
                },
            )
        return actual

    def _authorize(
        self, effect: WorkspaceEffect, approval_receipt: str | None
    ) -> Literal["NOT_REQUIRED", "CONSUMED"]:
        if effect.decision == "DENY":
            raise StructuredToolError("effect_denied", "Denied effects are never executable")
        if effect.decision == "ALLOW":
            if approval_receipt is not None:
                raise StructuredToolError(
                    "approval_not_expected", "ALLOW effects do not accept approval receipts"
                )
            return "NOT_REQUIRED"
        if approval_receipt is None:
            raise StructuredToolError("approval_required", "An exact approval receipt is required")
        if self.approval_verifier is None:
            raise StructuredToolError(
                "approval_verifier_unavailable", "No trusted approval verifier is configured"
            )
        try:
            self.approval_verifier(approval_receipt, effect)
        except StructuredToolError:
            raise
        except Exception as exc:
            raise StructuredToolError(
                "approval_verification_failed",
                "The approval receipt could not be verified safely",
                {"reason": type(exc).__name__},
            ) from exc
        return "CONSUMED"

    def _execute_write(
        self, effect: WorkspaceEffect, target: str, backup: str | None
    ) -> tuple[str, dict[str, Any]]:
        arguments = effect.final_arguments
        if not isinstance(arguments, WriteTextArguments):
            raise StructuredToolError("operation_argument_mismatch", "WRITE_TEXT arguments required")
        data = arguments.content_utf8.encode("utf-8")
        if len(data) > MAX_WRITE_TEXT_BYTES:
            raise StructuredToolError(
                "write_too_large", "WRITE_TEXT exceeds the configured UTF-8 byte limit"
            )
        parent, name = _parent_and_name(target)
        created_backup: tuple[str, str, int, int] | None = None
        with self.root.open_directory_descriptor(parent) as (parent_descriptor, _):
            metadata = _lstat_child(parent_descriptor, name)
            if metadata is None:
                if effect.recovery.mode != "REMOVE_CREATED_PATH" or backup is not None:
                    raise StructuredToolError(
                        "recovery_plan_mismatch", "New files must bind REMOVE_CREATED_PATH recovery"
                    )
                recovery = {"mode": "REMOVE_CREATED_PATH", "backup_path": None}
            else:
                if not stat.S_ISREG(metadata.st_mode):
                    raise StructuredToolError("not_regular_file", "WRITE_TEXT targets regular files")
                if effect.recovery.mode != "RESTORE_BACKUP" or backup is None:
                    raise StructuredToolError(
                        "recovery_plan_mismatch", "Overwrites require an exact backup path"
                    )
                opened, _ = open_child_descriptor(
                    parent_descriptor, name, expected=metadata, require_file=True
                )
                try:
                    old_bytes = bytearray()
                    os.lseek(opened, 0, os.SEEK_SET)
                    while chunk := os.read(opened, 1024 * 1024):
                        old_bytes.extend(chunk)
                    old_digest = hashlib.sha256(old_bytes).hexdigest()
                finally:
                    os.close(opened)
                backup_parent, backup_name = _parent_and_name(backup)
                with self.root.open_directory_descriptor(backup_parent) as (
                    backup_descriptor,
                    _,
                ):
                    if _lstat_child(backup_descriptor, backup_name) is not None:
                        raise StructuredToolError(
                            "recovery_backup_exists", "Recovery backup paths are single-use"
                        )
                    _atomic_write_bytes(backup_descriptor, backup_name, bytes(old_bytes))
                    backup_metadata = _lstat_child(backup_descriptor, backup_name)
                    if backup_metadata is None or not stat.S_ISREG(backup_metadata.st_mode):
                        raise StructuredToolError(
                            "filesystem_race_detected",
                            "The recovery backup changed after its atomic write",
                        )
                    created_backup = (
                        backup_parent,
                        backup_name,
                        backup_metadata.st_dev,
                        backup_metadata.st_ino,
                    )
                recovery = {
                    "mode": "RESTORE_BACKUP",
                    "backup_path": backup,
                    "backup_sha256": old_digest,
                }
            try:
                _atomic_write_bytes(parent_descriptor, name, data)
            except StructuredToolError:
                if created_backup is not None:
                    backup_parent, backup_name, expected_dev, expected_ino = created_backup
                    with self.root.open_directory_descriptor(backup_parent) as (
                        backup_descriptor,
                        _,
                    ):
                        current = _lstat_child(backup_descriptor, backup_name)
                        if current is not None and (
                            current.st_dev == expected_dev and current.st_ino == expected_ino
                        ):
                            try:
                                os.unlink(backup_name, dir_fd=backup_descriptor)
                                os.fsync(backup_descriptor)
                            except OSError:
                                pass
                raise
        return hashlib.sha256(data).hexdigest(), recovery

    def _execute_mkdir(
        self, effect: WorkspaceEffect, target: str, backup: str | None
    ) -> tuple[str, dict[str, Any]]:
        if not isinstance(effect.final_arguments, MakeDirectoryArguments):
            raise StructuredToolError(
                "operation_argument_mismatch", "MAKE_DIRECTORY arguments required"
            )
        if effect.recovery.mode != "REMOVE_CREATED_PATH" or backup is not None:
            raise StructuredToolError(
                "recovery_plan_mismatch", "Directories must bind REMOVE_CREATED_PATH recovery"
            )
        parent, name = _parent_and_name(target)
        with self.root.open_directory_descriptor(parent) as (parent_descriptor, _):
            if _lstat_child(parent_descriptor, name) is not None:
                raise StructuredToolError("path_already_exists", "Directory target already exists")
            try:
                os.mkdir(name, 0o755, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except OSError as exc:
                raise StructuredToolError(
                    "directory_create_failed",
                    "The workspace directory could not be created",
                    {"reason": type(exc).__name__},
                ) from exc
        return canonical_sha256({"kind": "directory", "path": target}), {
            "mode": "REMOVE_CREATED_PATH",
            "backup_path": None,
        }

    def _execute_delete(
        self, effect: WorkspaceEffect, target: str, backup: str | None
    ) -> tuple[str, dict[str, Any]]:
        if not isinstance(effect.final_arguments, DeleteFileArguments):
            raise StructuredToolError("operation_argument_mismatch", "DELETE_FILE arguments required")
        if effect.recovery.mode != "RESTORE_BACKUP" or backup is None:
            raise StructuredToolError(
                "recovery_plan_mismatch", "DELETE_FILE requires a recoverable backup"
            )
        target_parent, target_name = _parent_and_name(target)
        backup_parent, backup_name = _parent_and_name(backup)
        with self.root.open_directory_descriptor(target_parent) as (target_descriptor, _):
            metadata = _lstat_child(target_descriptor, target_name)
            if metadata is None:
                raise StructuredToolError("path_not_found", "DELETE_FILE target does not exist")
            if not stat.S_ISREG(metadata.st_mode):
                raise StructuredToolError("not_regular_file", "DELETE_FILE only removes regular files")
            opened, _ = open_child_descriptor(
                target_descriptor, target_name, expected=metadata, require_file=True
            )
            try:
                digest = descriptor_sha256(opened)
            finally:
                os.close(opened)
            with self.root.open_directory_descriptor(backup_parent) as (backup_descriptor, _):
                if _lstat_child(backup_descriptor, backup_name) is not None:
                    raise StructuredToolError(
                        "recovery_backup_exists", "Recovery backup paths are single-use"
                    )
                try:
                    os.replace(
                        target_name,
                        backup_name,
                        src_dir_fd=target_descriptor,
                        dst_dir_fd=backup_descriptor,
                    )
                    os.fsync(target_descriptor)
                    if backup_descriptor != target_descriptor:
                        os.fsync(backup_descriptor)
                except OSError as exc:
                    raise StructuredToolError(
                        "recoverable_delete_failed",
                        "The file could not be moved to its recovery backup",
                        {"reason": type(exc).__name__},
                    ) from exc
        return digest, {
            "mode": "RESTORE_BACKUP",
            "backup_path": backup,
            "backup_sha256": digest,
        }

    def execute(
        self, effect: WorkspaceEffect, *, approval_receipt: str | None = None
    ) -> dict[str, Any]:
        target, backup = self._validate_authority(effect)
        before = self._verify_checkpoint(effect)
        approval = self._authorize(effect, approval_receipt)
        self._verify_checkpoint(effect)

        if effect.operation == "WRITE_TEXT":
            artifact_sha256, recovery = self._execute_write(effect, target, backup)
        elif effect.operation == "MAKE_DIRECTORY":
            artifact_sha256, recovery = self._execute_mkdir(effect, target, backup)
        else:
            artifact_sha256, recovery = self._execute_delete(effect, target, backup)
        after = workspace_checkpoint_sha256(self.root.path, effect.project_id)
        core = {
            "schema": "egoagentos.workspace-effect-receipt.v1",
            "status": "APPLIED",
            "effect_sha256": effect.effect_sha256,
            "operation": effect.operation,
            "target": target,
            "affected_scope": effect.affected_scope,
            "project_id": effect.project_id,
            "task_id": effect.task_id,
            "policy_sha256": effect.policy_sha256,
            "decision": effect.decision,
            "approval": approval,
            "before_checkpoint_sha256": before,
            "after_checkpoint_sha256": after,
            "artifact_sha256": artifact_sha256,
            "recovery": recovery,
        }
        return {**core, "receipt_sha256": workspace_receipt_sha256(core)}
