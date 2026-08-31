from __future__ import annotations

import hashlib
import json
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
    MeasuredConfigurationId,
    ModelRequest,
    RequestClass,
    RunManifest,
    RunManifestCore,
    SignedTaskLeaseCore,
    SourceRef,
    TicketTemplate,
    validate_task_lease_core,
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
    assert _validate_lease(qualification).generation == 16
    qualification["generation"] = 17
    with pytest.raises(ValueError):
        _validate_lease(qualification)

    optimizer = lease_data("OPTIMIZER")
    optimizer.update(
        problem_id="__optimizer__",
        turn=1,
        generation=6,
        requirement_ledger_sha256=SHA["optimizer-input"],
        workspace_checkpoint_sha256=NO_WORKSPACE_CHECKPOINT_SHA256,
        memory_watermark=0,
    )
    assert _validate_lease(optimizer, optimizer_input_sha256=SHA["optimizer-input"]).generation == 6
    optimizer["workspace_checkpoint_sha256"] = SHA["checkpoint"]
    with pytest.raises(ValueError):
        _validate_lease(optimizer, optimizer_input_sha256=SHA["optimizer-input"])


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
