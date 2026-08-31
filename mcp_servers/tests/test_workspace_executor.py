from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from egoagentos_mcp.common import StructuredToolError


def _workspace_modules() -> tuple[Any, Any]:
    try:
        contract = importlib.import_module("egoagentos_mcp.workspace_contract")
        executor = importlib.import_module("egoagentos_mcp.workspace_executor")
    except ModuleNotFoundError:
        pytest.fail("typed workspace effect gateway modules are missing")
    return contract, executor


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "workspace-root"
    project = root / "project-alpha"
    project.mkdir(parents=True)
    monkeypatch.setenv("EGO_MCP_WORKSPACE_ROOT", str(root))
    return root, project


def _effect(
    root: Path,
    *,
    operation: str = "WRITE_TEXT",
    target: str = "project-alpha/notes/result.txt",
    final_arguments: dict[str, object] | None = None,
    decision: str = "ALLOW",
    recovery_mode: str = "REMOVE_CREATED_PATH",
    backup_path: str | None = None,
    checkpoint: str | None = None,
) -> Any:
    contract, executor = _workspace_modules()
    arguments = final_arguments or {
        "operation": operation,
        "content_utf8": "bounded workspace output\n",
    }
    scope = [target] if backup_path is None else [target, backup_path]
    core = {
        "schema": "egoagentos.workspace-effect.v1",
        "operation": operation,
        "final_arguments": arguments,
        "target": target,
        "affected_scope": scope,
        "project_id": "project-alpha",
        "task_id": "task-007",
        "workspace_checkpoint_sha256": checkpoint
        or executor.workspace_checkpoint_sha256(root, "project-alpha"),
        "policy_sha256": "a" * 64,
        "decision": decision,
        "reversibility": "REVERSIBLE",
        "recovery": {
            "mode": recovery_mode,
            "backup_path": backup_path,
        },
    }
    return contract.WorkspaceEffect.model_validate(
        {**core, "effect_sha256": contract.workspace_effect_sha256(core)}
    )


def test_write_text_is_atomic_and_returns_content_free_deterministic_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, project = _workspace(tmp_path, monkeypatch)
    (project / "notes").mkdir()
    contract, executor = _workspace_modules()
    effect = _effect(root)

    receipt = executor.WorkspaceExecutor.from_env().execute(effect)

    target = project / "notes/result.txt"
    assert target.read_text(encoding="utf-8") == "bounded workspace output\n"
    assert receipt == {
        **{key: receipt[key] for key in receipt if key not in {"receipt_sha256"}},
        "receipt_sha256": contract.workspace_receipt_sha256(
            {key: receipt[key] for key in receipt if key != "receipt_sha256"}
        ),
    }
    assert receipt["schema"] == "egoagentos.workspace-effect-receipt.v1"
    assert receipt["effect_sha256"] == effect.effect_sha256
    assert receipt["before_checkpoint_sha256"] == effect.workspace_checkpoint_sha256
    assert receipt["after_checkpoint_sha256"] != receipt["before_checkpoint_sha256"]
    assert receipt["artifact_sha256"]
    assert "bounded workspace output" not in repr(receipt)


@pytest.mark.parametrize(
    ("target", "code"),
    [
        ("/project-alpha/result.txt", "path_outside_trusted_root"),
        ("project-alpha/../outside.txt", "path_outside_trusted_root"),
        (r"project-alpha\outside.txt", "invalid_path"),
        ("project-alpha/bad\x00name.txt", "invalid_path"),
        ("project-alpha/.env", "sensitive_path_rejected"),
        ("project-beta/result.txt", "project_scope_mismatch"),
    ],
)
def test_untrusted_or_cross_project_targets_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    code: str,
) -> None:
    root, _project = _workspace(tmp_path, monkeypatch)
    effect = _effect(root, target=target)
    _contract, executor = _workspace_modules()

    with pytest.raises(StructuredToolError) as rejected:
        executor.WorkspaceExecutor.from_env().execute(effect)

    assert rejected.value.code == code
    assert sorted(path.name for path in root.iterdir()) == ["project-alpha"]


def test_argument_drift_and_stale_checkpoint_never_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, project = _workspace(tmp_path, monkeypatch)
    (project / "notes").mkdir()
    _contract, executor = _workspace_modules()
    approved = _effect(root)
    drifted = approved.model_copy(
        update={
            "final_arguments": approved.final_arguments.model_copy(
                update={"content_utf8": "drifted arguments"}
            )
        }
    )

    with pytest.raises(StructuredToolError) as drift:
        executor.WorkspaceExecutor.from_env().execute(drifted)
    assert drift.value.code == "effect_digest_mismatch"

    (project / "unrelated.txt").write_text("checkpoint changed", encoding="utf-8")
    with pytest.raises(StructuredToolError) as stale:
        executor.WorkspaceExecutor.from_env().execute(approved)
    assert stale.value.code == "workspace_checkpoint_stale"
    assert not (project / "notes/result.txt").exists()


def test_operation_argument_mismatch_unknown_operation_and_extra_fields_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _project = _workspace(tmp_path, monkeypatch)
    contract, _executor = _workspace_modules()
    valid = _effect(root).model_dump(mode="json")

    for update in (
        {"operation": "RUN_SHELL"},
        {"unexpected_authority": "agentteams said yes"},
        {
            "final_arguments": {
                "operation": "MAKE_DIRECTORY",
                "content_utf8": "not valid for mkdir",
            }
        },
    ):
        with pytest.raises(ValidationError):
            contract.WorkspaceEffect.model_validate({**valid, **update})


def test_make_directory_and_delete_file_are_reversible_and_non_recursive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, project = _workspace(tmp_path, monkeypatch)
    recovery = project / ".recovery"
    recovery.mkdir()
    _contract, executor = _workspace_modules()
    service = executor.WorkspaceExecutor.from_env()

    mkdir = _effect(
        root,
        operation="MAKE_DIRECTORY",
        target="project-alpha/generated",
        final_arguments={"operation": "MAKE_DIRECTORY"},
    )
    mkdir_receipt = service.execute(mkdir)
    assert (project / "generated").is_dir()
    assert mkdir_receipt["recovery"]["mode"] == "REMOVE_CREATED_PATH"

    target = project / "delete-me.txt"
    target.write_text("recoverable bytes", encoding="utf-8")
    delete = _effect(
        root,
        operation="DELETE_FILE",
        target="project-alpha/delete-me.txt",
        final_arguments={"operation": "DELETE_FILE"},
        recovery_mode="RESTORE_BACKUP",
        backup_path="project-alpha/.recovery/delete-me.bak",
    )
    delete_receipt = service.execute(delete)
    assert not target.exists()
    assert (recovery / "delete-me.bak").read_bytes() == b"recoverable bytes"
    assert delete_receipt["artifact_sha256"] == delete_receipt["recovery"]["backup_sha256"]

    populated = project / "directory"
    populated.mkdir()
    (populated / "child.txt").write_text("must remain", encoding="utf-8")
    recursive_delete = _effect(
        root,
        operation="DELETE_FILE",
        target="project-alpha/directory",
        final_arguments={"operation": "DELETE_FILE"},
        recovery_mode="RESTORE_BACKUP",
        backup_path="project-alpha/.recovery/directory.bak",
    )
    with pytest.raises(StructuredToolError) as rejected:
        service.execute(recursive_delete)
    assert rejected.value.code == "not_regular_file"
    assert (populated / "child.txt").exists()


class _ReceiptVerifier:
    def __init__(self, *, failure_code: str | None = None) -> None:
        self.failure_code = failure_code
        self.calls: list[tuple[str, str]] = []

    def __call__(self, approval_receipt: str, effect: Any) -> None:
        self.calls.append((approval_receipt, effect.effect_sha256))
        if self.failure_code is not None:
            raise StructuredToolError(self.failure_code, "Approval receipt was rejected")
        if approval_receipt != "exact-receipt-for-effect":
            raise StructuredToolError("approval_scope_mismatch", "Approval receipt did not match")


def test_decision_and_approval_receipt_gates_run_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, project = _workspace(tmp_path, monkeypatch)
    (project / "notes").mkdir()
    _contract, executor = _workspace_modules()
    verifier = _ReceiptVerifier()
    service = executor.WorkspaceExecutor(root, approval_verifier=verifier)

    denied = _effect(root, decision="DENY")
    with pytest.raises(StructuredToolError) as deny:
        service.execute(denied, approval_receipt="exact-receipt-for-effect")
    assert deny.value.code == "effect_denied"
    assert verifier.calls == []

    required = _effect(root, decision="APPROVAL_REQUIRED")
    with pytest.raises(StructuredToolError) as missing:
        service.execute(required)
    assert missing.value.code == "approval_required"
    with pytest.raises(StructuredToolError) as mismatch:
        service.execute(required, approval_receipt="receipt-for-other-effect")
    assert mismatch.value.code == "approval_scope_mismatch"
    assert not (project / "notes/result.txt").exists()

    receipt = service.execute(required, approval_receipt="exact-receipt-for-effect")
    assert receipt["approval"] == "CONSUMED"
    assert verifier.calls[-1] == ("exact-receipt-for-effect", required.effect_sha256)


@pytest.mark.parametrize("failure_code", ["approval_expired", "approval_replayed"])
def test_expired_or_replayed_approval_receipts_never_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_code: str,
) -> None:
    root, project = _workspace(tmp_path, monkeypatch)
    (project / "notes").mkdir()
    _contract, executor = _workspace_modules()
    service = executor.WorkspaceExecutor(
        root, approval_verifier=_ReceiptVerifier(failure_code=failure_code)
    )

    with pytest.raises(StructuredToolError) as rejected:
        service.execute(
            _effect(root, decision="APPROVAL_REQUIRED"),
            approval_receipt="stale-or-replayed-receipt",
        )

    assert rejected.value.code == failure_code
    assert not (project / "notes/result.txt").exists()


def test_symlink_and_special_file_races_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, project = _workspace(tmp_path, monkeypatch)
    notes = project / "notes"
    notes.mkdir()
    target = notes / "result.txt"
    target.write_text("inside", encoding="utf-8")
    recovery = project / ".recovery"
    recovery.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-marker", encoding="utf-8")
    _contract, executor = _workspace_modules()

    delete = _effect(
        root,
        operation="DELETE_FILE",
        target="project-alpha/notes/result.txt",
        final_arguments={"operation": "DELETE_FILE"},
        recovery_mode="RESTORE_BACKUP",
        backup_path="project-alpha/.recovery/result.bak",
    )
    original_open = executor.open_child_descriptor
    replaced = False

    def replace_then_open(parent_descriptor: int, name: str, **kwargs: object):
        nonlocal replaced
        if name == "result.txt" and kwargs.get("require_file") and not replaced:
            replaced = True
            target.rename(notes / "result-original.txt")
            target.symlink_to(outside)
        return original_open(parent_descriptor, name, **kwargs)

    monkeypatch.setattr(executor, "open_child_descriptor", replace_then_open)
    with pytest.raises(StructuredToolError) as raced:
        executor.WorkspaceExecutor.from_env().execute(delete)
    assert raced.value.code == "filesystem_race_detected"
    assert outside.read_text(encoding="utf-8") == "outside-marker"

    fifo = project / "named-pipe"
    os.mkfifo(fifo)
    with pytest.raises(StructuredToolError) as special:
        executor.workspace_checkpoint_sha256(root, "project-alpha")
    assert special.value.code == "special_file_rejected"


def test_atomic_write_failure_preserves_old_bytes_and_redacts_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, project = _workspace(tmp_path, monkeypatch)
    notes = project / "notes"
    notes.mkdir()
    target = notes / "result.txt"
    target.write_text("old bytes", encoding="utf-8")
    recovery = project / ".recovery"
    recovery.mkdir()
    _contract, executor = _workspace_modules()

    secret = "api_key=must-never-leak"
    secret_effect = _effect(
        root,
        final_arguments={"operation": "WRITE_TEXT", "content_utf8": secret},
        recovery_mode="RESTORE_BACKUP",
        backup_path="project-alpha/.recovery/result-secret.bak",
    )
    with pytest.raises(StructuredToolError) as secret_rejected:
        executor.WorkspaceExecutor.from_env().execute(secret_effect)
    assert secret_rejected.value.code == "secret_content_rejected"
    assert "must-never-leak" not in str(secret_rejected.value)

    effect = _effect(
        root,
        final_arguments={"operation": "WRITE_TEXT", "content_utf8": "new bytes"},
        recovery_mode="RESTORE_BACKUP",
        backup_path="project-alpha/.recovery/result.bak",
    )

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("api_key=replace-failure-secret")

    monkeypatch.setattr(executor.os, "replace", fail_replace)
    with pytest.raises(StructuredToolError) as failed:
        executor.WorkspaceExecutor.from_env().execute(effect)
    assert failed.value.code == "atomic_write_failed"
    assert "replace-failure-secret" not in str(failed.value)
    assert target.read_bytes() == b"old bytes"


def test_failed_overwrite_removes_its_recovery_backup_and_restores_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, project = _workspace(tmp_path, monkeypatch)
    notes = project / "notes"
    notes.mkdir()
    target = notes / "result.txt"
    target.write_text("old bytes", encoding="utf-8")
    recovery = project / ".recovery"
    recovery.mkdir()
    _contract, executor = _workspace_modules()
    effect = _effect(
        root,
        final_arguments={"operation": "WRITE_TEXT", "content_utf8": "new bytes"},
        recovery_mode="RESTORE_BACKUP",
        backup_path="project-alpha/.recovery/result.bak",
    )
    original_replace = executor.os.replace
    replace_calls = 0

    def fail_final_replace(*args: object, **kwargs: object) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("final replace failed")
        original_replace(*args, **kwargs)

    monkeypatch.setattr(executor.os, "replace", fail_final_replace)
    with pytest.raises(StructuredToolError) as failed:
        executor.WorkspaceExecutor.from_env().execute(effect)

    assert failed.value.code == "atomic_write_failed"
    assert target.read_bytes() == b"old bytes"
    assert not (recovery / "result.bak").exists()
    assert executor.workspace_checkpoint_sha256(root, "project-alpha") == (
        effect.workspace_checkpoint_sha256
    )
