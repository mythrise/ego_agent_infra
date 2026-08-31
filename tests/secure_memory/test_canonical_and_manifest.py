from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from benchmarks.secure_memory.canonical import (
    canonical_bytes,
    canonical_sha256,
    parse_json_bytes,
    validate_guest_artifact_path,
)
from benchmarks.secure_memory.manifest import (
    SchemaContractError,
    freeze_manifest,
    verify_schema_contract,
)
from benchmarks.secure_memory.models import (
    NO_WORKSPACE_CHECKPOINT_SHA256,
    CandidateProposal,
    ExecutionPhaseOwner,
    FactScope,
    ImageBinding,
    IssuedBudgetTicket,
    MeasuredConfigurationId,
    ModelRequest,
    ModelResponse,
    RequestClass,
    RunManifest,
    RunManifestCore,
    SignedTaskLeaseCore,
    SourceRef,
    TicketTemplate,
    TrustedFactCore,
    validate_task_lease_core,
)
from worker_distribution import (
    ArchiveMember,
    ArchiveMemberKind,
    PublicArtifactError,
    tar_archive_members,
    validate_public_worker_sdist,
    validate_public_worker_wheel,
    zip_archive_members,
)


SHA = {
    name: hashlib.sha256(name.encode("ascii")).hexdigest()
    for name in (
        "agentteams-resources",
        "agentteams-role-dag",
        "workspace-policy",
        "system-risk",
        "guardian",
        "projection",
        "glossary",
        "pi-reference",
        "codex-reference",
        "contract",
        "serializer",
        "scanner",
        "channel-keys",
        "agentteams-image",
        "agentteams-image-policy",
        "profiles",
        "schedule",
        "templates",
        "prompt-policy",
        "scenario-rubric",
        "qualification-matrix",
        "approval-fixtures",
        "rxp-snapshot",
        "optimizer-grid",
        "requirement",
        "checkpoint",
        "extension",
        "optimizer-input",
        "gpu-authorization",
    )
}


def _domain_digest(domain: str, value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256((f"egoagentos:{domain}:v1\0").encode("ascii") + payload).hexdigest()


def manifest_core_data() -> Dict[str, Any]:
    resources = SHA["agentteams-resources"]
    role_dag = SHA["agentteams-role-dag"]
    commit = "223ddc2b8073e4c8b93bcbb15e1d717f196c04d9"
    repository = "https://github.com/agentscope-ai/AgentTeams"
    workspace = SHA["workspace-policy"]
    system_risk = SHA["system-risk"]
    guardian = SHA["guardian"]
    projection = SHA["projection"]
    glossary = SHA["glossary"]
    return {
        "schema_version": "secure-memory-run-manifest/v2",
        "campaign_id": "campaign-test",
        "campaign_nonce": "nonce-test",
        "source_commit": "59e4ee937343278ddf320c78384433b8e56f4d8b",
        "egoagentos_commit": "037e534ea6ef805e804b95a2ae340b955f12d07f",
        "agentteams_repository": repository,
        "agentteams_commit": commit,
        "agentteams_contract_lock_sha256": _domain_digest(
            "agentteams-contract-lock",
            {
                "repository": repository,
                "commit": commit,
                "resources_sha256": resources,
                "role_dag_sha256": role_dag,
            },
        ),
        "agentteams_resources_sha256": resources,
        "agentteams_role_dag_sha256": role_dag,
        "workspace_tool_policy_sha256": workspace,
        "system_risk_rules_sha256": system_risk,
        "guardian_rules_sha256": guardian,
        "user_projection_policy_sha256": projection,
        "user_term_glossary_sha256": glossary,
        "effect_policy_bundle_sha256": _domain_digest(
            "effect-policy-bundle",
            {
                "workspace_tool_policy_sha256": workspace,
                "system_risk_rules_sha256": system_risk,
                "guardian_rules_sha256": guardian,
                "user_projection_policy_sha256": projection,
                "user_term_glossary_sha256": glossary,
            },
        ),
        "design_reference_digests": {
            "pi": SHA["pi-reference"],
            "codex": SHA["codex-reference"],
        },
        "provider_base_url": "https://apihub.agnes-ai.com/v1",
        "provider_model": "agnes-2.5-pro",
        "contract_sha256": SHA["contract"],
        "serializer_sha256": SHA["serializer"],
        "scanner_sha256": SHA["scanner"],
        "evaluator_public_key": "fake-evaluator-public-key",
        "controller_checkpoint_public_key": "fake-controller-public-key",
        "control_receipt_public_keys": {"control-1": "fake-control-public-key"},
        "channel_key_schedule_sha256": SHA["channel-keys"],
        "arms": ("A", "B", "C", "D", "E"),
        "images": (
            {
                "role": "agentteams",
                "image_sha256": SHA["agentteams-image"],
                "policy_sha256": SHA["agentteams-image-policy"],
            },
        ),
        "initial_configuration_profiles_sha256": SHA["profiles"],
        "randomization_seed": "independent-randomization-seed",
        "schedule_sha256": SHA["schedule"],
        "budget_ticket_template_set_sha256": SHA["templates"],
        "prompt_context_policy_sha256": SHA["prompt-policy"],
        "scenario_rubric_relevance_sha256": SHA["scenario-rubric"],
        "provider_qualification_matrix_sha256": SHA["qualification-matrix"],
        "approval_fixture_set_sha256": SHA["approval-fixtures"],
        "rxp_trust_snapshot_sha256": SHA["rxp-snapshot"],
        "absolute_request_cap": 360,
        "absolute_input_cap": 4_000_000,
        "absolute_output_cap": 600_000,
        "optimizer_grid_sha256": SHA["optimizer-grid"],
        "reserved_request_cap": 356,
        "reserved_input_cap": 3_306_000,
        "reserved_output_cap": 485_500,
    }


def frozen_manifest() -> RunManifest:
    return freeze_manifest(RunManifestCore.model_validate(manifest_core_data()))


def lease_data(owner: str = "A") -> Dict[str, Any]:
    configuration_id = owner if owner in {"A", "B", "C", "D", "E", "F"} else None
    return {
        "schema_version": "secure-memory-task-lease/v1",
        "campaign_id": "campaign-test",
        "configuration_id": configuration_id,
        "execution_phase_owner": owner,
        "problem_id": "problem-1",
        "turn": 2,
        "generation": 3,
        "manifest_sha256": frozen_manifest().manifest_sha256,
        "post_selection_extension_sha256": None,
        "policy_sha256": manifest_core_data()["effect_policy_bundle_sha256"],
        "requirement_ledger_sha256": SHA["requirement"],
        "workspace_checkpoint_sha256": SHA["checkpoint"],
        "memory_watermark": 7,
        "project_id": "project-1",
        "task_id": "task-1",
        "worker": "worker-1",
        "matrix_user_id": "@worker-1:matrix.test",
        "role": "Runtime",
        "stage": "EXECUTE",
        "allowed_skills": ("inspect", "test"),
        "allowed_tools": ("read", "write"),
        "request_class": "main",
        "issued_ticket_ids": ("ticket-1", "ticket-2"),
        "expires_at_sequence": 50,
        "issuer_id": "control",
        "key_id": "control-key-1",
        "issue_sequence": 10,
    }


def assert_invalid_model(model: Any, data: Dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_canonical_json_and_domain_digest_are_literal_and_deterministic() -> None:
    assert canonical_bytes({"é": "café", "z": [1, True, None]}) == (
        '{"z":[1,true,null],"é":"café"}'.encode("utf-8")
    )
    assert canonical_sha256("fixture", {"b": 2, "a": 1}) == (
        "6a8cbe4463c7ed18528a03adb2fd3291bae9b83438977dc357a6fb4d9e17cdb0"
    )
    assert NO_WORKSPACE_CHECKPOINT_SHA256 == (
        "0ad64c299329c3ed1194704ffd0e582aac970fe94cce76b70a6a1ba7c5a12a8b"
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"same":1,"same":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
    ],
)
def test_json_parser_rejects_duplicate_keys_and_non_finite_numbers(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_json_bytes(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"1e400",
        b"-1e400",
        b'{"outer":[1e400]}',
        b'{"outer":{"inner":-1e400}}',
    ],
)
def test_json_parser_rejects_exponent_overflow_at_every_depth(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_json_bytes(raw)


def test_strict_models_reject_unknown_fields_and_unsorted_evidence_digests() -> None:
    image = {
        "role": "agentteams",
        "image_sha256": SHA["agentteams-image"],
        "policy_sha256": SHA["agentteams-image-policy"],
        "manifest_sha256": SHA["contract"],
    }
    assert_invalid_model(ImageBinding, image)

    proposal = {
        "schema_version": "secure-memory-candidate/v1",
        "proposal_id": "proposal-1",
        "task_id": "task-1",
        "generation": 1,
        "claimed_fact_id": "fact-1",
        "statement_utf8_base64": "ZmFrZSBzdGF0ZW1lbnQ=",
        "memory_type": "semantic",
        "component": "bridge",
        "outcome_claim": "KEEP",
        "applicability_scope": {"project_id": "project-1", "component": "bridge"},
        "source_refs": ({"kind": "test", "identifier": "case-1"},),
        "support_digest_claims": (SHA["workspace-policy"], SHA["agentteams-resources"]),
    }
    assert_invalid_model(CandidateProposal, proposal)


@pytest.mark.parametrize(
    "arms",
    [
        ("A", "B", "C", "D"),
        ("A", "B", "C", "D", "E", "F"),
        ("A", "B", "C", "E", "D"),
        ("A", "B", "C", "D", "D"),
    ],
)
def test_manifest_accepts_only_the_exact_initial_a_through_e_arm_tuple(arms: Any) -> None:
    data = manifest_core_data()
    data["arms"] = arms
    assert_invalid_model(RunManifestCore, data)


def test_manifest_rejects_unqualified_provider_and_non_agentteams_runtime() -> None:
    data = manifest_core_data()
    data["provider_base_url"] = "http://apihub.agnes-ai.com/v1"
    assert_invalid_model(RunManifestCore, data)

    data = manifest_core_data()
    data["provider_model"] = "another-model"
    assert_invalid_model(RunManifestCore, data)

    request = {
        "schema_version": "secure-memory-model-request/v1",
        "request_id": "request-1",
        "campaign_id": "campaign-test",
        "lease_sha256": SHA["contract"],
        "ticket_id": "ticket-1",
        "request_class": "main",
        "provider_base_url": "https://apihub.agnes-ai.com/v1",
        "provider_model": "agnes-2.5-pro",
        "runtime": "Pi",
        "messages": ({"role": "user", "content": "fake fixture"},),
        "max_input_tokens": 100,
        "max_output_tokens": 50,
        "temperature": 0,
        "top_p": 1,
        "stream": False,
        "tools": (),
    }
    assert_invalid_model(ModelRequest, request)
    request["runtime"] = "Codex"
    assert_invalid_model(ModelRequest, request)


def test_manifest_rejects_agentteams_resource_or_effect_policy_mismatch() -> None:
    data = manifest_core_data()
    data["agentteams_resources_sha256"] = SHA["contract"]
    assert_invalid_model(RunManifestCore, data)

    data = manifest_core_data()
    data["effect_policy_bundle_sha256"] = SHA["contract"]
    assert_invalid_model(RunManifestCore, data)


def test_freezer_hashes_only_the_validated_core_and_rejects_self_reference() -> None:
    core = RunManifestCore.model_validate(manifest_core_data())
    manifest = freeze_manifest(core)
    assert manifest.manifest_sha256 == _domain_digest("run-manifest", core.model_dump(mode="json"))

    self_referential = manifest_core_data()
    self_referential["manifest_sha256"] = SHA["contract"]
    assert_invalid_model(RunManifestCore, self_referential)

    template = {
        "schema_version": "secure-memory-ticket-template/v1",
        "template_id": "template-1",
        "purpose": "initial-runtime",
        "execution_phase_owner": "A",
        "configuration_id": "A",
        "problem_id": "problem-1",
        "turn": 1,
        "allowed_role": "Runtime",
        "request_class": "main",
        "usage_phase": "architecture",
        "slot_id": "runtime-1",
        "attempt_group": "initial",
        "retry_owner": None,
        "max_input_tokens": 10_000,
        "max_output_tokens": 1_500,
        "manifest_sha256": manifest.manifest_sha256,
    }
    assert_invalid_model(TicketTemplate, template)


def _validate_lease(data: Dict[str, Any], **context: Any) -> SignedTaskLeaseCore:
    core = SignedTaskLeaseCore.model_validate(data)
    return validate_task_lease_core(core, frozen_manifest(), **context)


def test_initial_lease_requires_exact_current_release_and_sorted_capabilities() -> None:
    context = {
        "released_problem_id": "problem-1",
        "released_turn": 2,
        "released_generation": 3,
        "current_requirement_ledger_sha256": SHA["requirement"],
        "current_workspace_checkpoint_sha256": SHA["checkpoint"],
        "current_memory_watermark": 7,
    }
    assert _validate_lease(lease_data(), **context).configuration_id == MeasuredConfigurationId.A

    wrong_arm = lease_data()
    wrong_arm["configuration_id"] = "B"
    with pytest.raises(ValueError):
        _validate_lease(wrong_arm, **context)

    stale = lease_data()
    stale["memory_watermark"] = 6
    with pytest.raises(ValueError):
        _validate_lease(stale, **context)

    unsorted = lease_data()
    unsorted["allowed_tools"] = ("write", "read")
    assert_invalid_model(SignedTaskLeaseCore, unsorted)

    duplicate = lease_data()
    duplicate["issued_ticket_ids"] = ("ticket-1", "ticket-1")
    assert_invalid_model(SignedTaskLeaseCore, duplicate)


@pytest.mark.parametrize(
    ("owner", "configuration_id"),
    [
        (ExecutionPhaseOwner.F, "F"),
        (ExecutionPhaseOwner.WINNER_SEALED, "D"),
        (ExecutionPhaseOwner.F_SEALED, "F"),
    ],
)
def test_post_selection_owners_require_the_verified_extension(
    owner: ExecutionPhaseOwner, configuration_id: str
) -> None:
    data = lease_data(owner.value)
    data["configuration_id"] = configuration_id
    data["post_selection_extension_sha256"] = SHA["extension"]
    context = {
        "released_problem_id": "problem-1",
        "released_turn": 2,
        "released_generation": 3,
        "current_requirement_ledger_sha256": SHA["requirement"],
        "current_workspace_checkpoint_sha256": SHA["checkpoint"],
        "current_memory_watermark": 7,
        "verified_post_selection_extension_sha256": SHA["extension"],
        "selected_original_configuration_id": MeasuredConfigurationId.D,
    }
    assert _validate_lease(data, **context).execution_phase_owner == owner
    data["post_selection_extension_sha256"] = None
    with pytest.raises(ValueError):
        _validate_lease(data, **context)


def test_qualification_and_optimizer_lease_sentinels_are_exact() -> None:
    qualification = lease_data("QUALIFICATION")
    qualification.update(
        problem_id="__qualification__",
        turn=1,
        generation=16,
        requirement_ledger_sha256=SHA["qualification-matrix"],
        workspace_checkpoint_sha256=NO_WORKSPACE_CHECKPOINT_SHA256,
        memory_watermark=0,
    )
    assert _validate_lease(qualification, qualification_case_index=16).generation == 16
    qualification["generation"] = 17
    with pytest.raises(ValueError):
        _validate_lease(qualification, qualification_case_index=16)

    optimizer = lease_data("OPTIMIZER")
    optimizer.update(
        problem_id="__optimizer__",
        turn=1,
        generation=6,
        requirement_ledger_sha256=SHA["optimizer-input"],
        workspace_checkpoint_sha256=NO_WORKSPACE_CHECKPOINT_SHA256,
        memory_watermark=0,
    )
    assert (
        _validate_lease(
            optimizer,
            optimizer_input_sha256=SHA["optimizer-input"],
            optimizer_proposal_index=6,
        ).generation
        == 6
    )
    optimizer["workspace_checkpoint_sha256"] = SHA["checkpoint"]
    with pytest.raises(ValueError):
        _validate_lease(
            optimizer,
            optimizer_input_sha256=SHA["optimizer-input"],
            optimizer_proposal_index=6,
        )


@pytest.mark.parametrize("configuration_id", ["C", "F"])
def test_gpu_demo_lease_binds_authorization_checkpoint_and_conditional_extension(
    configuration_id: str,
) -> None:
    gpu = lease_data("GPU_DEMO")
    gpu.update(
        configuration_id=configuration_id,
        problem_id="__gpu_demo__",
        turn=1,
        generation=1,
        requirement_ledger_sha256=SHA["gpu-authorization"],
        post_selection_extension_sha256=(SHA["extension"] if configuration_id == "F" else None),
    )
    context = {
        "gpu_selected_configuration_id": MeasuredConfigurationId(configuration_id),
        "gpu_authorization_core_sha256": SHA["gpu-authorization"],
        "current_workspace_checkpoint_sha256": SHA["checkpoint"],
        "current_memory_watermark": 7,
        "verified_post_selection_extension_sha256": SHA["extension"],
    }
    assert _validate_lease(gpu, **context).problem_id == "__gpu_demo__"
    gpu["requirement_ledger_sha256"] = SHA["requirement"]
    with pytest.raises(ValueError):
        _validate_lease(gpu, **context)


def test_gpu_demo_for_f_rejects_absent_extension_in_lease_and_authoritative_context() -> None:
    gpu = lease_data("GPU_DEMO")
    gpu.update(
        configuration_id="F",
        problem_id="__gpu_demo__",
        turn=1,
        generation=1,
        requirement_ledger_sha256=SHA["gpu-authorization"],
        post_selection_extension_sha256=None,
    )
    with pytest.raises(ValueError):
        _validate_lease(
            gpu,
            gpu_selected_configuration_id=MeasuredConfigurationId.F,
            gpu_authorization_core_sha256=SHA["gpu-authorization"],
            current_workspace_checkpoint_sha256=SHA["checkpoint"],
            current_memory_watermark=7,
            verified_post_selection_extension_sha256=None,
        )


@pytest.mark.parametrize(
    "path",
    ["/guest/evidence.json", "../evidence.json", "safe/../../evidence.json", "C:\\evidence.json"],
)
def test_guest_artifact_paths_reject_absolute_and_parent_traversal(path: str) -> None:
    with pytest.raises(ValueError):
        validate_guest_artifact_path(path)


def test_wire_support_types_are_strict_and_constructible() -> None:
    assert FactScope(project_id="project-1", component="bridge").project_id == "project-1"
    assert SourceRef(kind="test", identifier="case-1").kind == "test"
    assert RequestClass.MAIN.value == "main"


def test_schema_digest_index_covers_every_public_schema_and_rejects_incomplete_index(
    tmp_path: Path,
) -> None:
    verify_schema_contract()
    incomplete = tmp_path / "contract-digests.json"
    incomplete.write_text(
        json.dumps(
            {"schema_version": "secure-agent-contract-digests/v1", "schemas": {}},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    with pytest.raises(SchemaContractError, match="missing"):
        verify_schema_contract(index_path=incomplete)


def test_qualification_requires_exact_authoritative_case_index() -> None:
    qualification = lease_data("QUALIFICATION")
    qualification.update(
        problem_id="__qualification__",
        turn=1,
        generation=8,
        requirement_ledger_sha256=SHA["qualification-matrix"],
        workspace_checkpoint_sha256=NO_WORKSPACE_CHECKPOINT_SHA256,
        memory_watermark=0,
    )
    with pytest.raises(ValueError):
        _validate_lease(qualification)
    with pytest.raises(ValueError):
        _validate_lease(qualification, qualification_case_index=7)
    assert _validate_lease(qualification, qualification_case_index=8).generation == 8


def test_optimizer_requires_exact_authoritative_proposal_index() -> None:
    optimizer = lease_data("OPTIMIZER")
    optimizer.update(
        problem_id="__optimizer__",
        turn=1,
        generation=4,
        requirement_ledger_sha256=SHA["optimizer-input"],
        workspace_checkpoint_sha256=NO_WORKSPACE_CHECKPOINT_SHA256,
        memory_watermark=0,
    )
    context = {"optimizer_input_sha256": SHA["optimizer-input"]}
    with pytest.raises(ValueError):
        _validate_lease(optimizer, **context)
    with pytest.raises(ValueError):
        _validate_lease(optimizer, optimizer_proposal_index=3, **context)
    assert (
        _validate_lease(optimizer, optimizer_proposal_index=4, **context).generation == 4
    )


def model_request_data(request_class: str, max_input: int, max_output: int) -> Dict[str, Any]:
    return {
        "schema_version": "secure-memory-model-request/v1",
        "request_id": "request-limits",
        "campaign_id": "campaign-test",
        "lease_sha256": SHA["contract"],
        "ticket_id": "ticket-limits",
        "request_class": request_class,
        "provider_base_url": "https://apihub.agnes-ai.com/v1",
        "provider_model": "agnes-2.5-pro",
        "runtime": "agentteams",
        "messages": ({"role": "user", "content": "fake fixture"},),
        "max_input_tokens": max_input,
        "max_output_tokens": max_output,
        "temperature": 0,
        "top_p": 1,
        "stream": False,
        "tools": (),
    }


def ticket_template_data(request_class: str, max_input: int, max_output: int) -> Dict[str, Any]:
    return {
        "schema_version": "secure-memory-ticket-template/v1",
        "template_id": "template-limits",
        "purpose": "initial-runtime",
        "execution_phase_owner": "A",
        "configuration_id": "A",
        "problem_id": "problem-1",
        "turn": 1,
        "allowed_role": "Runtime",
        "request_class": request_class,
        "usage_phase": "architecture",
        "slot_id": "runtime-1",
        "attempt_group": "initial",
        "retry_owner": None,
        "max_input_tokens": max_input,
        "max_output_tokens": max_output,
    }


def issued_ticket_data(request_class: str, max_input: int, max_output: int) -> Dict[str, Any]:
    return {
        "schema_version": "secure-memory-issued-budget-ticket/v1",
        "ticket_id": "ticket-limits",
        "template_id": "template-limits",
        "campaign_id": "campaign-test",
        "manifest_sha256": SHA["contract"],
        "execution_phase_owner": "A",
        "configuration_id": "A",
        "project_id": "project-1",
        "task_id": "task-1",
        "worker": "worker-1",
        "matrix_user_id": "@worker-1:matrix.test",
        "allowed_role": "Runtime",
        "effective_request_class": request_class,
        "usage_phase": "architecture",
        "max_input_tokens": max_input,
        "max_output_tokens": max_output,
        "expires_at_sequence": 20,
        "issuer_id": "control",
        "key_id": "control-key-1",
        "issue_sequence": 10,
        "ticket_sha256": SHA["templates"],
        "signature_base64": "ZmFrZS1zaWduYXR1cmU=",
    }


@pytest.mark.parametrize(
    ("model", "factory", "request_class", "allowed", "prohibited"),
    [
        (ModelRequest, model_request_data, "main", (10_000, 1_500), (10_001, 1_501)),
        (ModelRequest, model_request_data, "auxiliary", (6_000, 750), (6_001, 751)),
        (ModelRequest, model_request_data, "review", (8_000, 1_000), (8_001, 1_001)),
        (TicketTemplate, ticket_template_data, "main", (10_000, 1_500), (10_001, 1_501)),
        (TicketTemplate, ticket_template_data, "auxiliary", (6_000, 750), (6_001, 751)),
        (TicketTemplate, ticket_template_data, "review", (8_000, 1_000), (8_001, 1_001)),
        (IssuedBudgetTicket, issued_ticket_data, "main", (10_000, 1_500), (10_001, 1_501)),
        (IssuedBudgetTicket, issued_ticket_data, "auxiliary", (6_000, 750), (6_001, 751)),
        (IssuedBudgetTicket, issued_ticket_data, "review", (8_000, 1_000), (8_001, 1_001)),
    ],
)
def test_request_class_controls_every_request_and_ticket_ceiling(
    model: Any,
    factory: Any,
    request_class: str,
    allowed: Any,
    prohibited: Any,
) -> None:
    assert model.model_validate(factory(request_class, *allowed)).max_output_tokens == allowed[1]
    assert_invalid_model(model, factory(request_class, prohibited[0], allowed[1]))
    assert_invalid_model(model, factory(request_class, allowed[0], prohibited[1]))


def _schema(filename: str) -> Dict[str, Any]:
    path = Path("benchmarks/secure_memory/schemas") / filename
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_exported_schemas_publish_expressible_canonical_constraints() -> None:
    manifest_schema = _schema("run-manifest-v2.schema.json")
    arms = manifest_schema["$defs"]["RunManifestCore"]["properties"]["arms"]
    assert arms["const"] == ["A", "B", "C", "D", "E"]

    lease_schema = _schema("signed-task-lease-v1.schema.json")
    lease_core = lease_schema["$defs"]["SignedTaskLeaseCore"]["properties"]
    for field in ("allowed_skills", "allowed_tools", "issued_ticket_ids"):
        assert lease_core[field]["uniqueItems"] is True
        assert lease_core[field]["x-canonical-order"] == "ascending"

    checkpoint_schema = _schema("checkpoint-v1.schema.json")
    path_schema = checkpoint_schema["properties"]["workspace_overlay_path"]
    assert path_schema["format"] == "canonical-relative-posix-path"
    assert "pattern" in path_schema

    for filename, definition in (
        ("candidate-proposal-v1.schema.json", None),
        ("trusted-fact-v1.schema.json", None),
    ):
        schema = _schema(filename)
        properties = schema["properties"] if definition is None else schema["$defs"][definition]
        statement = properties["statement_utf8_base64"]
        assert statement["contentEncoding"] == "base64"
        assert statement["contentMediaType"] == "text/plain; charset=utf-8"
        assert "pattern" in statement

    for filename, class_field in (
        ("model-request-v1.schema.json", "request_class"),
        ("ticket-template-v1.schema.json", "request_class"),
        ("issued-budget-ticket-v1.schema.json", "effective_request_class"),
    ):
        conditions = _schema(filename)["allOf"]
        observed = {
            item["if"]["properties"][class_field]["const"]: (
                item["then"]["properties"]["max_input_tokens"]["maximum"],
                item["then"]["properties"]["max_output_tokens"]["maximum"],
            )
            for item in conditions
        }
        assert observed == {
            "main": (10_000, 1_500),
            "auxiliary": (6_000, 750),
            "review": (8_000, 1_000),
        }


def test_every_exported_schema_requires_the_canonical_semantic_validator() -> None:
    for filename in (
        "run-manifest-v2.schema.json",
        "channel-envelope-v2.schema.json",
        "model-request-v1.schema.json",
        "model-response-v1.schema.json",
        "ticket-template-v1.schema.json",
        "issued-budget-ticket-v1.schema.json",
        "signed-task-lease-v1.schema.json",
        "candidate-proposal-v1.schema.json",
        "trusted-fact-v1.schema.json",
        "trusted-relation-v1.schema.json",
        "checkpoint-v1.schema.json",
        "campaign-event-v1.schema.json",
    ):
        schema = _schema(filename)
        assert schema["x-canonical-semantic-validator"] == (
            "benchmarks.secure_memory.manifest.validate_wire_document"
        )


def test_schema_semantic_entry_point_rejects_cross_field_and_digest_bypasses() -> None:
    from benchmarks.secure_memory import manifest as manifest_module

    validator = getattr(manifest_module, "validate_wire_document")
    bad_core = manifest_core_data()
    bad_core["arms"] = ["A", "B", "C", "D", "F"]
    raw = canonical_bytes(
        {
            "core": bad_core,
            "manifest_sha256": _domain_digest("run-manifest", bad_core),
        }
    )
    with pytest.raises(ValueError):
        validator("run-manifest-v2.schema.json", raw)

    valid_core = manifest_core_data()
    with pytest.raises(ValueError):
        validator(
            "run-manifest-v2.schema.json",
            canonical_bytes(
                {"core": valid_core, "manifest_sha256": SHA["contract"]}
            ),
        )

    with pytest.raises(ValueError):
        validator(
            "candidate-proposal-v1.schema.json",
            canonical_bytes(candidate_proposal_data("Zh==")),
        )

    checkpoint = {
        "schema_version": "secure-memory-checkpoint/v1",
        "campaign_id": "campaign-test",
        "configuration_id": "A",
        "problem_id": "problem-1",
        "turn": 1,
        "generation": 1,
        "source_seed_sha256": SHA["contract"],
        "workspace_overlay_path": "../workspace-overlay.qcow2",
        "workspace_overlay_sha256": SHA["checkpoint"],
        "tree_sha256": SHA["schedule"],
        "patch_sha256": SHA["profiles"],
        "requirement_ledger_sha256": SHA["requirement"],
        "memory_watermark": 0,
        "agentteams_project_id": "project-1",
        "workflow_root_sha256": SHA["agentteams-role-dag"],
        "room_root_sha256": SHA["agentteams-resources"],
        "budget_state_sha256": SHA["templates"],
        "channel_epochs_sha256": SHA["channel-keys"],
        "previous_checkpoint_sha256": None,
        "issue_sequence": 1,
    }
    with pytest.raises(ValueError):
        validator("checkpoint-v1.schema.json", canonical_bytes(checkpoint))

    qualification = lease_data("QUALIFICATION")
    qualification.update(
        problem_id="__qualification__",
        turn=1,
        generation=8,
        requirement_ledger_sha256=SHA["qualification-matrix"],
        workspace_checkpoint_sha256=NO_WORKSPACE_CHECKPOINT_SHA256,
        memory_watermark=0,
    )
    signed = {
        "core": qualification,
        "core_sha256": _domain_digest("task-lease-core", qualification),
        "signature_base64": "ZmFrZS1zaWduYXR1cmU=",
    }
    with pytest.raises(ValueError):
        validator(
            "signed-task-lease-v1.schema.json",
            canonical_bytes(signed),
            manifest=frozen_manifest(),
            lease_context={},
        )

    qualification["allowed_tools"] = ("write", "read")
    signed["core"] = qualification
    signed["core_sha256"] = _domain_digest("task-lease-core", qualification)
    with pytest.raises(ValueError):
        validator(
            "signed-task-lease-v1.schema.json",
            canonical_bytes(signed),
            manifest=frozen_manifest(),
            lease_context={"qualification_case_index": 8},
        )


def candidate_proposal_data(statement: str) -> Dict[str, Any]:
    return {
        "schema_version": "secure-memory-candidate/v1",
        "proposal_id": "proposal-base64",
        "task_id": "task-1",
        "generation": 1,
        "claimed_fact_id": "fact-1",
        "statement_utf8_base64": statement,
        "memory_type": "semantic",
        "component": "bridge",
        "outcome_claim": "KEEP",
        "applicability_scope": {"project_id": "project-1", "component": "bridge"},
        "source_refs": ({"kind": "test", "identifier": "case-1"},),
        "support_digest_claims": (SHA["agentteams-resources"],),
    }


def trusted_fact_data(statement: str) -> Dict[str, Any]:
    return {
        "schema_version": "secure-memory-trusted-fact/v1",
        "fact_id": "fact-1",
        "fact_kind": "test-result",
        "statement_utf8_base64": statement,
        "outcome": "KEEP",
        "applicability_scope": {"project_id": "project-1", "component": "bridge"},
        "source_refs": ({"kind": "test", "identifier": "case-1"},),
        "support_digests": (SHA["agentteams-resources"],),
    }


@pytest.mark.parametrize(
    ("model", "factory", "statement"),
    [
        (CandidateProposal, candidate_proposal_data, "Zh=="),
        (CandidateProposal, candidate_proposal_data, "//8="),
        (TrustedFactCore, trusted_fact_data, "not-base64"),
        (TrustedFactCore, trusted_fact_data, "Zh=="),
        (TrustedFactCore, trusted_fact_data, "//8="),
    ],
)
def test_statement_bytes_require_exact_canonical_utf8_base64(
    model: Any, factory: Any, statement: str
) -> None:
    assert_invalid_model(model, factory(statement))


def test_canonical_utf8_base64_round_trips_exact_unicode_bytes() -> None:
    statement = "5Y+v5L+h55qE6K+B5o2u"
    proposal = CandidateProposal.model_validate(candidate_proposal_data(statement))
    fact = TrustedFactCore.model_validate(trusted_fact_data(statement))
    assert proposal.statement_utf8_base64 == statement
    assert fact.statement_utf8_base64 == statement


def model_response_data(tool_calls: Any) -> Dict[str, Any]:
    return {
        "schema_version": "secure-memory-model-response/v1",
        "request_id": "request-1",
        "response_id": "response-1",
        "provider_model": "agnes-2.5-pro",
        "output_text": "fake response",
        "tool_calls": tool_calls,
        "finish_reason": "stop",
        "input_tokens": 10,
        "output_tokens": 5,
        "cached_input_tokens": 0,
        "reasoning_output_tokens": 0,
    }


@pytest.mark.parametrize(
    "tool_calls",
    [
        ({"name": "fake", "arguments": {"value": float("nan")}},),
        ({"name": "fake", "arguments": {"value": float("inf")}},),
        ({"name": "fake", "arguments": {1: "non-string-key"}},),
        ({"name": "fake", "arguments": {"value": {"unsupported"}}},),
        ({"name": "fake", "arguments": {"value": "\ud800"}},),
    ],
)
def test_model_response_rejects_noncanonical_json_tool_calls(tool_calls: Any) -> None:
    assert_invalid_model(ModelResponse, model_response_data(tool_calls))


def test_offline_worker_wheel_contains_only_public_secure_contracts(tmp_path: Path) -> None:
    source = _copy_worker_build_source(tmp_path)
    output = tmp_path / "wheel"
    result = _build_worker_wheel(source, output)
    assert result.returncode == 0, result.stderr
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        members = zip_archive_members(wheel.infolist())
        validate_public_worker_wheel(members)
        with pytest.raises(PublicArtifactError):
            validate_public_worker_wheel(
                [
                    *members,
                    ArchiveMember(
                        "benchmarks/secure_memory/schemas/unreviewed-public-data.json",
                        ArchiveMemberKind.REGULAR_FILE,
                    ),
                ]
            )
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        entry_points_name = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        metadata = wheel.read(metadata_name).decode("utf-8")
        entry_points = wheel.read(entry_points_name).decode("utf-8")
        extracted = tmp_path / "extracted"
        wheel.extractall(extracted)

    prohibited = []
    for name in names:
        parts = Path(name).parts
        stem = Path(name).stem.lower()
        if stem in {"evaluator", "sealed", "hidden"} or any(
            part.lower() in {"evaluator", "sealed", "hidden"} for part in parts
        ):
            prohibited.append(name)
    assert prohibited == []
    assert "Requires-Python: >=3.9" in metadata
    assert "rxp-bench = benchmarks.runner:main" in entry_points
    assert "benchmarks/secure_memory/models.py" in names
    assert "benchmarks/secure_memory/substrate/channel.py" in names
    assert "benchmarks/secure_memory/substrate/candidate_rpc.py" in names
    assert "benchmarks/secure_memory/schemas/channel-envelope-v2.schema.json" in names
    assert "benchmarks/secure_memory/schemas/run-manifest-v2.schema.json" in names

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(extracted)
    smoke = subprocess.run(
        [
            sys.executable,
            "-c",
            "from benchmarks.runner import main; main(['--help'])",
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert smoke.returncode == 0, smoke.stderr
    assert "versioned RXP benchmark corpus" in smoke.stdout


_WORKER_SOURCE_DIRECTORIES = (
    "apps",
    "benchmarks",
    "experiments",
    "integrations",
    "protocols",
    "semifinal_acceptance",
    "skill_runtime",
    "skills",
)
_STALE_PRIVATE_PATHS = (
    "apps/api/evaluator.py",
    "benchmarks/secure_memory/hidden/secret.py",
    "benchmarks/secure_memory/schemas/sealed-data.json",
    "benchmarks/secure_memory/schemas/hidden-fixture.json",
    "benchmarks/secure_memory/schemas/evaluator.schema.json",
    "benchmarks/secure_memory/schemas/sealed_data.json",
    "benchmarks/secure_memory/schemas/hidden.fixture.json",
    "benchmarks/secure_memory/schemas/evaluator-fixture.json",
)


def _copy_worker_build_source(destination: Path) -> Path:
    source = destination / "source"
    source.mkdir()
    for filename in (
        "LICENSE",
        "README.md",
        "pyproject.toml",
        "setup.py",
        "worker_distribution.py",
    ):
        shutil.copy2(filename, source / filename)
    for directory in _WORKER_SOURCE_DIRECTORIES:
        shutil.copytree(directory, source / directory)
    return source


def _build_worker_wheel(source: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "build", "--offline", "--wheel", "--out-dir", str(output)],
        cwd=source,
        check=False,
        capture_output=True,
        text=True,
    )


def _build_worker_sdist(source: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "build", "--offline", "--sdist", "--out-dir", str(output)],
        cwd=source,
        check=False,
        capture_output=True,
        text=True,
    )


def test_worker_wheel_fails_closed_on_stale_private_staging(tmp_path: Path) -> None:
    source = _copy_worker_build_source(tmp_path)
    first = _build_worker_wheel(source, tmp_path / "first-wheel")
    assert first.returncode == 0, first.stderr

    for relative in _STALE_PRIVATE_PATHS:
        target = source / "build/lib" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("private sentinel\n", encoding="utf-8")

    second_output = tmp_path / "second-wheel"
    second = _build_worker_wheel(source, second_output)
    if second.returncode == 0:
        wheel_path = next(second_output.glob("*.whl"))
        with zipfile.ZipFile(wheel_path) as wheel:
            members = zip_archive_members(wheel.infolist())
        with pytest.raises(PublicArtifactError) as rejected:
            validate_public_worker_wheel(members)
        pytest.fail(f"stale private staging was accepted: {rejected.value}")
    assert "stale public Worker staging" in second.stderr


@pytest.mark.parametrize(
    ("relative", "artifact_flag"),
    [
        ("benchmarks/secure_memory/schemas/sealed-data.json", "--wheel"),
        ("benchmarks/secure_memory/schemas/hidden.fixture.json", "--sdist"),
        ("benchmarks/secure_memory/evaluator_fixture.py", "--wheel"),
    ],
)
def test_worker_build_rejects_ambiguous_private_source(
    tmp_path: Path, relative: str, artifact_flag: str
) -> None:
    source = _copy_worker_build_source(tmp_path)
    private_source = source / relative
    private_source.parent.mkdir(parents=True, exist_ok=True)
    private_source.write_text("private sentinel\n", encoding="utf-8")
    output = tmp_path / "artifact"
    result = subprocess.run(
        ["uv", "build", "--offline", artifact_flag, "--out-dir", str(output)],
        cwd=source,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Worker" in result.stdout + result.stderr


def test_offline_worker_sdist_contains_only_public_build_inputs(tmp_path: Path) -> None:
    source = _copy_worker_build_source(tmp_path)
    output = tmp_path / "sdist"
    result = _build_worker_sdist(source, output)
    assert result.returncode == 0, result.stderr
    sdist = next(output.glob("*.tar.gz"))
    with tarfile.open(sdist, "r:gz") as archive:
        members = tar_archive_members(archive.getmembers())
    validate_public_worker_sdist(members)


def test_worker_wheel_rebuilt_from_sdist_preserves_public_boundary(tmp_path: Path) -> None:
    source = _copy_worker_build_source(tmp_path)
    sdist_output = tmp_path / "sdist"
    sdist_result = _build_worker_sdist(source, sdist_output)
    assert sdist_result.returncode == 0, sdist_result.stderr
    sdist = next(sdist_output.glob("*.tar.gz"))
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(sdist, "r:gz") as archive:
        archive.extractall(extracted)
    rebuilt_source = next(path for path in extracted.iterdir() if path.is_dir())

    wheel_output = tmp_path / "rebuilt-wheel"
    wheel_result = _build_worker_wheel(rebuilt_source, wheel_output)
    assert wheel_result.returncode == 0, wheel_result.stderr
    wheel = next(wheel_output.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        validate_public_worker_wheel(zip_archive_members(archive.infolist()))


@pytest.fixture(scope="module")
def production_worker_archives(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("typed-worker-archives")
    source = _copy_worker_build_source(root)
    output = root / "artifacts"
    wheel_result = _build_worker_wheel(source, output)
    assert wheel_result.returncode == 0, wheel_result.stderr
    sdist_result = _build_worker_sdist(source, output)
    assert sdist_result.returncode == 0, sdist_result.stderr
    return next(output.glob("*.whl")), next(output.glob("*.tar.gz"))


def _mutate_wheel_member(
    source_path: Path,
    destination: Path,
    target: str,
    replacement_kind: str,
) -> None:
    with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(destination, "w") as output:
        for member in source.infolist():
            if member.filename != target:
                output.writestr(member, source.read(member))

        replacement_name = target + "/" if replacement_kind == "directory" else target
        replacement = zipfile.ZipInfo(replacement_name)
        replacement.create_system = 3
        if replacement_kind == "directory":
            replacement.external_attr = (stat.S_IFDIR | 0o755) << 16
            replacement.external_attr |= 0x10
            content = b""
        elif replacement_kind == "symlink":
            replacement.external_attr = (stat.S_IFLNK | 0o777) << 16
            content = b"models.py"
        elif replacement_kind == "fifo":
            replacement.external_attr = (stat.S_IFIFO | 0o600) << 16
            content = b""
        else:
            replacement.external_attr = (stat.S_IFCHR | 0o600) << 16
            content = b""
        output.writestr(replacement, content)


@pytest.mark.parametrize(
    ("target", "replacement_kind"),
    [
        ("benchmarks/secure_memory/models.py", "directory"),
        ("egoagentos_researchops-0.1.0.dist-info/METADATA", "directory"),
        ("benchmarks/secure_memory/models.py", "symlink"),
        ("egoagentos_researchops-0.1.0.dist-info/METADATA", "symlink"),
        ("benchmarks/secure_memory/models.py", "fifo"),
        ("egoagentos_researchops-0.1.0.dist-info/METADATA", "character-device"),
    ],
)
def test_worker_wheel_oracle_rejects_typed_member_substitution(
    production_worker_archives: tuple[Path, Path],
    tmp_path: Path,
    target: str,
    replacement_kind: str,
) -> None:
    wheel, _sdist = production_worker_archives
    mutated = tmp_path / f"mutated-{replacement_kind}.whl"
    _mutate_wheel_member(wheel, mutated, target, replacement_kind)
    with zipfile.ZipFile(mutated) as archive, pytest.raises(PublicArtifactError):
        validate_public_worker_wheel(zip_archive_members(archive.infolist()))


def _copy_tar_member(
    source: tarfile.TarFile, output: tarfile.TarFile, member: tarfile.TarInfo
) -> None:
    fileobj = source.extractfile(member) if member.isfile() else None
    output.addfile(member, fileobj)


def _mutate_sdist_member(
    source_path: Path,
    destination: Path,
    mutation_kind: str,
) -> None:
    prefix = "egoagentos_researchops-0.1.0"
    required = f"{prefix}/benchmarks/secure_memory/models.py"
    replace_required = mutation_kind == "required-directory"
    with tarfile.open(source_path, "r:gz") as source, tarfile.open(
        destination, "w:gz"
    ) as output:
        for member in source.getmembers():
            if not (replace_required and member.name == required):
                _copy_tar_member(source, output, member)

        if mutation_kind == "symlink":
            replacement = tarfile.TarInfo(f"{prefix}/benchmarks/secure_memory/hidden-link")
            replacement.type = tarfile.SYMTYPE
            replacement.linkname = required
        elif mutation_kind == "hardlink":
            replacement = tarfile.TarInfo(f"{prefix}/benchmarks/secure_memory/hard-link")
            replacement.type = tarfile.LNKTYPE
            replacement.linkname = required
        elif mutation_kind == "fifo":
            replacement = tarfile.TarInfo(f"{prefix}/benchmarks/secure_memory/worker-fifo")
            replacement.type = tarfile.FIFOTYPE
        elif mutation_kind == "character-device":
            replacement = tarfile.TarInfo(f"{prefix}/benchmarks/secure_memory/worker-char")
            replacement.type = tarfile.CHRTYPE
            replacement.devmajor = 1
            replacement.devminor = 3
        elif mutation_kind == "block-device":
            replacement = tarfile.TarInfo(f"{prefix}/benchmarks/secure_memory/worker-block")
            replacement.type = tarfile.BLKTYPE
            replacement.devmajor = 1
            replacement.devminor = 0
        elif mutation_kind == "required-directory":
            replacement = tarfile.TarInfo(required)
            replacement.type = tarfile.DIRTYPE
            replacement.mode = 0o755
        elif mutation_kind == "unexpected-directory":
            replacement = tarfile.TarInfo(f"{prefix}/benchmarks/secure_memory/unexpected")
            replacement.type = tarfile.DIRTYPE
            replacement.mode = 0o755
        elif mutation_kind == "duplicate":
            replacement = tarfile.TarInfo(required)
            content = b"duplicate regular member\n"
            replacement.size = len(content)
            output.addfile(replacement, io.BytesIO(content))
            return
        else:
            replacement = tarfile.TarInfo(
                f"{prefix}/./benchmarks/secure_memory/alias-member.py"
            )
            content = b"canonical alias\n"
            replacement.size = len(content)
            output.addfile(replacement, io.BytesIO(content))
            return
        output.addfile(replacement)


@pytest.mark.parametrize(
    "mutation_kind",
    [
        "symlink",
        "hardlink",
        "fifo",
        "character-device",
        "block-device",
        "required-directory",
        "unexpected-directory",
        "duplicate",
        "canonical-alias",
    ],
)
def test_worker_sdist_oracle_rejects_every_typed_member_violation(
    production_worker_archives: tuple[Path, Path],
    tmp_path: Path,
    mutation_kind: str,
) -> None:
    _wheel, sdist = production_worker_archives
    mutated = tmp_path / f"mutated-{mutation_kind}.tar.gz"
    _mutate_sdist_member(sdist, mutated, mutation_kind)
    with tarfile.open(mutated, "r:gz") as archive, pytest.raises(PublicArtifactError):
        validate_public_worker_sdist(tar_archive_members(archive.getmembers()))
