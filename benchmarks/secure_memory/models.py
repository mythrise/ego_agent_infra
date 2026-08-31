from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Dict, Literal, Mapping, Optional, Tuple

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import (
    canonical_bytes,
    canonical_sha256,
    validate_canonical_utf8_base64,
    validate_guest_artifact_path,
    validate_sha256_digest,
)


Digest = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{64}$"),
    AfterValidator(validate_sha256_digest),
]
GuestArtifactPath = Annotated[str, AfterValidator(validate_guest_artifact_path)]
CanonicalUtf8Base64 = Annotated[
    str,
    Field(min_length=1),
    AfterValidator(validate_canonical_utf8_base64),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    @model_validator(mode="after")
    def validate_canonical_json_value(self) -> "StrictModel":
        try:
            canonical_bytes(self)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc
        return self


class MeasuredConfigurationId(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"


class ExecutionPhaseOwner(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    QUALIFICATION = "QUALIFICATION"
    OPTIMIZER = "OPTIMIZER"
    WINNER_SEALED = "WINNER_SEALED"
    F_SEALED = "F_SEALED"
    GPU_DEMO = "GPU_DEMO"


class RequestClass(str, Enum):
    MAIN = "main"
    AUXILIARY = "auxiliary"
    REVIEW = "review"


REQUEST_CLASS_TOKEN_CEILINGS: Mapping[RequestClass, Tuple[int, int]] = {
    RequestClass.MAIN: (10_000, 1_500),
    RequestClass.AUXILIARY: (6_000, 750),
    RequestClass.REVIEW: (8_000, 1_000),
}


def _validate_request_class_ceiling(
    request_class: RequestClass,
    max_input_tokens: int,
    max_output_tokens: int,
) -> None:
    input_ceiling, output_ceiling = REQUEST_CLASS_TOKEN_CEILINGS[request_class]
    if max_input_tokens > input_ceiling or max_output_tokens > output_ceiling:
        raise ValueError(
            f"{request_class.value} request exceeds {input_ceiling}/{output_ceiling} token ceilings"
        )


def _sorted_unique(values: Tuple[str, ...], field_name: str) -> Tuple[str, ...]:
    if tuple(sorted(values)) != values or len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be sorted and duplicate-free")
    return values


def _sorted_unique_models(values: Tuple[StrictModel, ...], field_name: str) -> Tuple[StrictModel, ...]:
    encodings = tuple(canonical_bytes(value) for value in values)
    if tuple(sorted(encodings)) != encodings or len(set(encodings)) != len(encodings):
        raise ValueError(f"{field_name} must be canonically sorted and duplicate-free")
    return values


class ImageBinding(StrictModel):
    role: Literal["agentteams", "workspace", "control", "candidate_runner", "evaluator"]
    image_sha256: Digest
    policy_sha256: Digest


class RunManifestCore(StrictModel):
    schema_version: Literal["secure-memory-run-manifest/v2"]
    campaign_id: str = Field(min_length=1)
    campaign_nonce: str = Field(min_length=1)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    egoagentos_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    agentteams_repository: Literal["https://github.com/agentscope-ai/AgentTeams"]
    agentteams_commit: Literal["223ddc2b8073e4c8b93bcbb15e1d717f196c04d9"]
    agentteams_contract_lock_sha256: Digest
    agentteams_resources_sha256: Digest
    agentteams_role_dag_sha256: Digest
    workspace_tool_policy_sha256: Digest
    system_risk_rules_sha256: Digest
    guardian_rules_sha256: Digest
    user_projection_policy_sha256: Digest
    user_term_glossary_sha256: Digest
    effect_policy_bundle_sha256: Digest
    design_reference_digests: Dict[Literal["pi", "codex"], Digest]
    provider_base_url: Literal["https://apihub.agnes-ai.com/v1"]
    provider_model: Literal["agnes-2.5-pro"]
    contract_sha256: Digest
    serializer_sha256: Digest
    scanner_sha256: Digest
    evaluator_public_key: str = Field(min_length=1)
    controller_checkpoint_public_key: str = Field(min_length=1)
    control_receipt_public_keys: Dict[str, str]
    channel_key_schedule_sha256: Digest
    arms: Tuple[MeasuredConfigurationId, ...]
    images: Tuple[ImageBinding, ...]
    initial_configuration_profiles_sha256: Digest
    randomization_seed: str = Field(min_length=1)
    schedule_sha256: Digest
    budget_ticket_template_set_sha256: Digest
    prompt_context_policy_sha256: Digest
    scenario_rubric_relevance_sha256: Digest
    provider_qualification_matrix_sha256: Digest
    approval_fixture_set_sha256: Digest
    rxp_trust_snapshot_sha256: Digest
    absolute_request_cap: Literal[360]
    absolute_input_cap: Literal[4_000_000]
    absolute_output_cap: Literal[600_000]
    optimizer_grid_sha256: Digest
    reserved_request_cap: Literal[356]
    reserved_input_cap: Literal[3_306_000]
    reserved_output_cap: Literal[485_500]

    @model_validator(mode="after")
    def validate_frozen_bindings(self) -> "RunManifestCore":
        exact_arms = tuple(MeasuredConfigurationId(value) for value in "ABCDE")
        if self.arms != exact_arms:
            raise ValueError("initial RunManifest arms must be exactly A, B, C, D, E")
        if set(self.design_reference_digests) != {"pi", "codex"}:
            raise ValueError("design_reference_digests must contain exactly pi and codex")
        if not self.control_receipt_public_keys:
            raise ValueError("at least one Control receipt public key is required")

        expected_lock = canonical_sha256(
            "agentteams-contract-lock",
            {
                "repository": self.agentteams_repository,
                "commit": self.agentteams_commit,
                "resources_sha256": self.agentteams_resources_sha256,
                "role_dag_sha256": self.agentteams_role_dag_sha256,
            },
        )
        if self.agentteams_contract_lock_sha256 != expected_lock:
            raise ValueError("AgentTeams commit/resources/role DAG do not match the contract lock")

        expected_policy = canonical_sha256(
            "effect-policy-bundle",
            {
                "workspace_tool_policy_sha256": self.workspace_tool_policy_sha256,
                "system_risk_rules_sha256": self.system_risk_rules_sha256,
                "guardian_rules_sha256": self.guardian_rules_sha256,
                "user_projection_policy_sha256": self.user_projection_policy_sha256,
                "user_term_glossary_sha256": self.user_term_glossary_sha256,
            },
        )
        if self.effect_policy_bundle_sha256 != expected_policy:
            raise ValueError("effect policy bundle does not match its fixed preimage")
        return self


class RunManifest(StrictModel):
    core: RunManifestCore
    manifest_sha256: Digest

    @model_validator(mode="after")
    def validate_manifest_digest(self) -> "RunManifest":
        expected = canonical_sha256("run-manifest", self.core)
        if self.manifest_sha256 != expected:
            raise ValueError("manifest_sha256 does not match the canonical core")
        return self


class ModelRequest(StrictModel):
    schema_version: Literal["secure-memory-model-request/v1"]
    request_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    lease_sha256: Digest
    ticket_id: str = Field(min_length=1)
    request_class: RequestClass
    provider_base_url: Literal["https://apihub.agnes-ai.com/v1"]
    provider_model: Literal["agnes-2.5-pro"]
    runtime: Literal["agentteams"]
    messages: Tuple[Dict[str, Any], ...]
    max_input_tokens: int = Field(ge=1, le=10_000)
    max_output_tokens: int = Field(ge=1, le=1_500)
    temperature: Optional[Literal[0]]
    top_p: Optional[Literal[1]]
    stream: bool
    tools: Tuple[Dict[str, Any], ...]

    @field_validator("messages", "tools")
    @classmethod
    def validate_canonical_payloads(cls, values: Tuple[Dict[str, Any], ...]) -> Tuple[Dict[str, Any], ...]:
        canonical_bytes(values)
        return values

    @model_validator(mode="after")
    def validate_class_ceiling(self) -> "ModelRequest":
        _validate_request_class_ceiling(
            self.request_class,
            self.max_input_tokens,
            self.max_output_tokens,
        )
        return self


class ModelResponse(StrictModel):
    schema_version: Literal["secure-memory-model-response/v1"]
    request_id: str = Field(min_length=1)
    response_id: str = Field(min_length=1)
    provider_model: Literal["agnes-2.5-pro"]
    output_text: str
    tool_calls: Tuple[Dict[str, Any], ...]
    finish_reason: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: Optional[int] = Field(default=None, ge=0)
    reasoning_output_tokens: Optional[int] = Field(default=None, ge=0)


class TicketTemplate(StrictModel):
    schema_version: Literal["secure-memory-ticket-template/v1"]
    template_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    execution_phase_owner: ExecutionPhaseOwner
    configuration_id: Optional[MeasuredConfigurationId] = None
    problem_id: Optional[str] = None
    turn: Optional[int] = Field(default=None, ge=1, le=5)
    allowed_role: str = Field(min_length=1)
    request_class: RequestClass
    usage_phase: Literal["qualification", "architecture", "evaluation", "optimizer", "sealed", "gpu_demo"]
    slot_id: str = Field(min_length=1)
    attempt_group: str = Field(min_length=1)
    retry_owner: Optional[ExecutionPhaseOwner] = None
    max_input_tokens: int = Field(ge=1, le=10_000)
    max_output_tokens: int = Field(ge=1, le=1_500)

    @model_validator(mode="after")
    def validate_owner_binding(self) -> "TicketTemplate":
        initial = {
            ExecutionPhaseOwner.A,
            ExecutionPhaseOwner.B,
            ExecutionPhaseOwner.C,
            ExecutionPhaseOwner.D,
            ExecutionPhaseOwner.E,
            ExecutionPhaseOwner.F,
        }
        if self.execution_phase_owner in initial:
            if self.configuration_id is None or self.configuration_id.value != self.execution_phase_owner.value:
                raise ValueError("measured template owner must match configuration_id")
        elif self.execution_phase_owner in {ExecutionPhaseOwner.QUALIFICATION, ExecutionPhaseOwner.OPTIMIZER}:
            if self.configuration_id is not None:
                raise ValueError("qualification/optimizer templates cannot bind a configuration")
        _validate_request_class_ceiling(
            self.request_class,
            self.max_input_tokens,
            self.max_output_tokens,
        )
        return self


class IssuedBudgetTicket(StrictModel):
    schema_version: Literal["secure-memory-issued-budget-ticket/v1"]
    ticket_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    manifest_sha256: Digest
    execution_phase_owner: ExecutionPhaseOwner
    configuration_id: Optional[MeasuredConfigurationId]
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    worker: str = Field(min_length=1)
    matrix_user_id: str = Field(min_length=1)
    allowed_role: str = Field(min_length=1)
    effective_request_class: RequestClass
    usage_phase: str = Field(min_length=1)
    max_input_tokens: int = Field(ge=1, le=10_000)
    max_output_tokens: int = Field(ge=1, le=1_500)
    expires_at_sequence: int = Field(ge=0)
    issuer_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    issue_sequence: int = Field(ge=0)
    ticket_sha256: Digest
    signature_base64: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_class_ceiling(self) -> "IssuedBudgetTicket":
        _validate_request_class_ceiling(
            self.effective_request_class,
            self.max_input_tokens,
            self.max_output_tokens,
        )
        return self


class SignedTaskLeaseCore(StrictModel):
    schema_version: Literal["secure-memory-task-lease/v1"]
    campaign_id: str = Field(min_length=1)
    configuration_id: Optional[MeasuredConfigurationId]
    execution_phase_owner: ExecutionPhaseOwner
    problem_id: str = Field(min_length=1)
    turn: int = Field(ge=1, le=5)
    generation: int = Field(ge=1)
    manifest_sha256: Digest
    post_selection_extension_sha256: Optional[Digest]
    policy_sha256: Digest
    requirement_ledger_sha256: Digest
    workspace_checkpoint_sha256: Digest
    memory_watermark: int = Field(ge=0)
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    worker: str = Field(min_length=1)
    matrix_user_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    allowed_skills: Tuple[str, ...]
    allowed_tools: Tuple[str, ...]
    request_class: RequestClass
    issued_ticket_ids: Tuple[str, ...]
    expires_at_sequence: int = Field(ge=0)
    issuer_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    issue_sequence: int = Field(ge=0)

    @field_validator("allowed_skills", "allowed_tools", "issued_ticket_ids")
    @classmethod
    def validate_sorted_identifiers(cls, values: Tuple[str, ...], info: Any) -> Tuple[str, ...]:
        return _sorted_unique(values, info.field_name)


class SignedTaskLease(StrictModel):
    core: SignedTaskLeaseCore
    core_sha256: Digest
    signature_base64: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_core_digest(self) -> "SignedTaskLease":
        if self.core_sha256 != canonical_sha256("task-lease-core", self.core):
            raise ValueError("core_sha256 does not match the canonical lease core")
        return self


class FactScope(StrictModel):
    tenant_id: Optional[str] = None
    project_id: str = Field(min_length=1)
    component: str = Field(min_length=1)
    version: Optional[str] = None
    problem_id: Optional[str] = None


class SourceRef(StrictModel):
    kind: str = Field(min_length=1)
    identifier: str = Field(min_length=1)


class CandidateProposal(StrictModel):
    schema_version: Literal["secure-memory-candidate/v1"]
    proposal_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    generation: int = Field(ge=1)
    claimed_fact_id: Optional[str]
    statement_utf8_base64: CanonicalUtf8Base64
    memory_type: Literal["semantic", "episodic", "procedural"]
    component: str = Field(min_length=1)
    outcome_claim: Literal["KEEP", "DROP", "INCONCLUSIVE"]
    applicability_scope: FactScope
    source_refs: Tuple[SourceRef, ...]
    support_digest_claims: Tuple[Digest, ...]

    @field_validator("support_digest_claims")
    @classmethod
    def validate_support_digests(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _sorted_unique(values, "support_digest_claims")

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, values: Tuple[SourceRef, ...]) -> Tuple[SourceRef, ...]:
        return _sorted_unique_models(values, "source_refs")  # type: ignore[return-value]


class TrustedFactCore(StrictModel):
    schema_version: Literal["secure-memory-trusted-fact/v1"]
    fact_id: str = Field(min_length=1)
    fact_kind: str = Field(min_length=1)
    statement_utf8_base64: CanonicalUtf8Base64
    outcome: Literal["KEEP", "DROP", "INCONCLUSIVE"]
    applicability_scope: FactScope
    source_refs: Tuple[SourceRef, ...]
    support_digests: Tuple[Digest, ...]

    @field_validator("support_digests")
    @classmethod
    def validate_support_digests(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _sorted_unique(values, "support_digests")

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, values: Tuple[SourceRef, ...]) -> Tuple[SourceRef, ...]:
        return _sorted_unique_models(values, "source_refs")  # type: ignore[return-value]


class TrustedRelationCore(StrictModel):
    schema_version: Literal["secure-memory-trusted-relation/v1"]
    relation_id: str = Field(min_length=1)
    relation_type: Literal[
        "SUPPORTED_BY",
        "CONTRADICTS",
        "SUPERSEDES",
        "DEPENDS_ON",
        "APPLIES_TO",
        "CAUSED_BY",
        "FAILED_UNDER",
        "VERIFIED_BY",
    ]
    source_fact_sha256: Digest
    target_fact_sha256: Digest
    applicability_scope: FactScope
    source_refs: Tuple[SourceRef, ...]
    support_digests: Tuple[Digest, ...]

    @field_validator("support_digests")
    @classmethod
    def validate_support_digests(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _sorted_unique(values, "support_digests")

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, values: Tuple[SourceRef, ...]) -> Tuple[SourceRef, ...]:
        return _sorted_unique_models(values, "source_refs")  # type: ignore[return-value]


class CheckpointCore(StrictModel):
    schema_version: Literal["secure-memory-checkpoint/v1"]
    campaign_id: str = Field(min_length=1)
    configuration_id: MeasuredConfigurationId
    problem_id: str = Field(min_length=1)
    turn: int = Field(ge=1, le=5)
    generation: int = Field(ge=1)
    source_seed_sha256: Digest
    workspace_overlay_path: GuestArtifactPath
    workspace_overlay_sha256: Digest
    tree_sha256: Digest
    patch_sha256: Digest
    requirement_ledger_sha256: Digest
    memory_watermark: int = Field(ge=0)
    agentteams_project_id: str = Field(min_length=1)
    workflow_root_sha256: Digest
    room_root_sha256: Digest
    budget_state_sha256: Digest
    channel_epochs_sha256: Digest
    previous_checkpoint_sha256: Optional[Digest]
    issue_sequence: int = Field(ge=1)


class CampaignEventCore(StrictModel):
    schema_version: Literal["secure-memory-campaign-event/v1"]
    sequence: int = Field(ge=1)
    event_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    run_id: Optional[str]
    configuration_id: Optional[MeasuredConfigurationId]
    event_type: str = Field(min_length=1)
    monotonic_ns: int = Field(ge=0)
    payload: Dict[str, Any]
    previous_sha256: Digest

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        canonical_bytes(value)
        return value


NO_WORKSPACE_CHECKPOINT_SHA256: Digest = canonical_sha256(
    "absence",
    {"schema_version": "absence/v1", "kind": "workspace_checkpoint"},
)


def _require_equal(actual: Any, expected: Any, field_name: str) -> None:
    if expected is None or actual != expected:
        raise ValueError(f"lease {field_name} does not match the authoritative value")


def _validate_released_problem(
    core: SignedTaskLeaseCore,
    *,
    released_problem_id: Optional[str],
    released_turn: Optional[int],
    released_generation: Optional[int],
    current_requirement_ledger_sha256: Optional[str],
    current_workspace_checkpoint_sha256: Optional[str],
    current_memory_watermark: Optional[int],
) -> None:
    if core.problem_id.startswith("__"):
        raise ValueError("measured lease requires a real problem ID")
    _require_equal(core.problem_id, released_problem_id, "problem_id")
    _require_equal(core.turn, released_turn, "turn")
    _require_equal(core.generation, released_generation, "generation")
    _require_equal(
        core.requirement_ledger_sha256,
        current_requirement_ledger_sha256,
        "requirement_ledger_sha256",
    )
    _require_equal(
        core.workspace_checkpoint_sha256,
        current_workspace_checkpoint_sha256,
        "workspace_checkpoint_sha256",
    )
    _require_equal(core.memory_watermark, current_memory_watermark, "memory_watermark")


def validate_task_lease_core(
    core: SignedTaskLeaseCore,
    manifest: RunManifest,
    *,
    released_problem_id: Optional[str] = None,
    released_turn: Optional[int] = None,
    released_generation: Optional[int] = None,
    current_requirement_ledger_sha256: Optional[str] = None,
    current_workspace_checkpoint_sha256: Optional[str] = None,
    current_memory_watermark: Optional[int] = None,
    verified_post_selection_extension_sha256: Optional[str] = None,
    selected_original_configuration_id: Optional[MeasuredConfigurationId] = None,
    qualification_case_index: Optional[int] = None,
    optimizer_input_sha256: Optional[str] = None,
    optimizer_proposal_index: Optional[int] = None,
    gpu_selected_configuration_id: Optional[MeasuredConfigurationId] = None,
    gpu_authorization_core_sha256: Optional[str] = None,
) -> SignedTaskLeaseCore:
    """Validate a lease against manifest truth and owner-specific authoritative state."""

    _require_equal(core.campaign_id, manifest.core.campaign_id, "campaign_id")
    _require_equal(core.manifest_sha256, manifest.manifest_sha256, "manifest_sha256")
    _require_equal(
        core.policy_sha256,
        manifest.core.effect_policy_bundle_sha256,
        "policy_sha256",
    )

    initial_owners = {
        ExecutionPhaseOwner.A,
        ExecutionPhaseOwner.B,
        ExecutionPhaseOwner.C,
        ExecutionPhaseOwner.D,
        ExecutionPhaseOwner.E,
    }
    post_selection_owners = {
        ExecutionPhaseOwner.F,
        ExecutionPhaseOwner.WINNER_SEALED,
        ExecutionPhaseOwner.F_SEALED,
    }
    if core.execution_phase_owner in initial_owners:
        if core.configuration_id is None or core.configuration_id.value != core.execution_phase_owner.value:
            raise ValueError("initial owner must match configuration_id")
        if core.configuration_id not in manifest.core.arms:
            raise ValueError("initial configuration must be present in the A-E manifest")
        if core.post_selection_extension_sha256 is not None:
            raise ValueError("initial A-E lease cannot bind a post-selection extension")
        _validate_released_problem(
            core,
            released_problem_id=released_problem_id,
            released_turn=released_turn,
            released_generation=released_generation,
            current_requirement_ledger_sha256=current_requirement_ledger_sha256,
            current_workspace_checkpoint_sha256=current_workspace_checkpoint_sha256,
            current_memory_watermark=current_memory_watermark,
        )
    elif core.execution_phase_owner in post_selection_owners:
        _require_equal(
            core.post_selection_extension_sha256,
            verified_post_selection_extension_sha256,
            "post_selection_extension_sha256",
        )
        if core.execution_phase_owner in {ExecutionPhaseOwner.F, ExecutionPhaseOwner.F_SEALED}:
            if core.configuration_id is not MeasuredConfigurationId.F:
                raise ValueError("F and F_SEALED owners require configuration F")
        else:
            if selected_original_configuration_id not in {
                MeasuredConfigurationId.C,
                MeasuredConfigurationId.D,
                MeasuredConfigurationId.E,
            }:
                raise ValueError("WINNER_SEALED requires a selected original C/D/E winner")
            _require_equal(
                core.configuration_id,
                selected_original_configuration_id,
                "configuration_id",
            )
        _validate_released_problem(
            core,
            released_problem_id=released_problem_id,
            released_turn=released_turn,
            released_generation=released_generation,
            current_requirement_ledger_sha256=current_requirement_ledger_sha256,
            current_workspace_checkpoint_sha256=current_workspace_checkpoint_sha256,
            current_memory_watermark=current_memory_watermark,
        )
    elif core.execution_phase_owner is ExecutionPhaseOwner.QUALIFICATION:
        expected = (
            core.configuration_id is None
            and core.post_selection_extension_sha256 is None
            and core.problem_id == "__qualification__"
            and core.turn == 1
            and 1 <= core.generation <= 16
            and qualification_case_index is not None
            and 1 <= qualification_case_index <= 16
            and core.generation == qualification_case_index
            and core.requirement_ledger_sha256
            == manifest.core.provider_qualification_matrix_sha256
            and core.workspace_checkpoint_sha256 == NO_WORKSPACE_CHECKPOINT_SHA256
            and core.memory_watermark == 0
        )
        if not expected:
            raise ValueError("qualification lease sentinel fields do not match the frozen contract")
    elif core.execution_phase_owner is ExecutionPhaseOwner.OPTIMIZER:
        expected = (
            core.configuration_id is None
            and core.post_selection_extension_sha256 is None
            and core.problem_id == "__optimizer__"
            and core.turn == 1
            and 1 <= core.generation <= 6
            and optimizer_proposal_index is not None
            and 1 <= optimizer_proposal_index <= 6
            and core.generation == optimizer_proposal_index
            and optimizer_input_sha256 is not None
            and core.requirement_ledger_sha256 == optimizer_input_sha256
            and core.workspace_checkpoint_sha256 == NO_WORKSPACE_CHECKPOINT_SHA256
            and core.memory_watermark == 0
        )
        if not expected:
            raise ValueError("optimizer lease sentinel fields do not match the stored optimizer input")
    elif core.execution_phase_owner is ExecutionPhaseOwner.GPU_DEMO:
        if gpu_selected_configuration_id not in {
            MeasuredConfigurationId.C,
            MeasuredConfigurationId.D,
            MeasuredConfigurationId.E,
            MeasuredConfigurationId.F,
        }:
            raise ValueError("GPU_DEMO requires a selected C/D/E/F configuration")
        if gpu_selected_configuration_id is MeasuredConfigurationId.F:
            extension_is_valid = (
                verified_post_selection_extension_sha256 is not None
                and core.post_selection_extension_sha256
                == verified_post_selection_extension_sha256
            )
        else:
            extension_is_valid = core.post_selection_extension_sha256 is None
        expected = (
            core.configuration_id == gpu_selected_configuration_id
            and core.problem_id == "__gpu_demo__"
            and core.turn == 1
            and core.generation == 1
            and gpu_authorization_core_sha256 is not None
            and core.requirement_ledger_sha256 == gpu_authorization_core_sha256
            and current_workspace_checkpoint_sha256 is not None
            and core.workspace_checkpoint_sha256 == current_workspace_checkpoint_sha256
            and current_memory_watermark is not None
            and core.memory_watermark == current_memory_watermark
            and extension_is_valid
        )
        if not expected:
            raise ValueError("GPU_DEMO lease fields do not match the signed lane authorization")
    else:  # pragma: no cover - exhaustive enum guard
        raise ValueError("unsupported execution phase owner")
    return core
