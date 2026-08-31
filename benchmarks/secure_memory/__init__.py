"""Canonical public contracts for the secure AgentTeams memory benchmark."""

from typing import Any

from .canonical import (
    canonical_bytes,
    canonical_sha256,
    parse_json_bytes,
    validate_canonical_utf8_base64,
    validate_guest_artifact_path,
    validate_sha256_digest,
)
from .models import (
    NO_WORKSPACE_CHECKPOINT_SHA256,
    CampaignEventCore,
    CandidateProposal,
    CheckpointCore,
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
    SignedTaskLease,
    SignedTaskLeaseCore,
    SourceRef,
    TicketTemplate,
    TrustedFactCore,
    TrustedRelationCore,
    validate_task_lease_core,
)


def freeze_manifest(core: RunManifestCore) -> RunManifest:
    """Lazily import the freezer so ``python -m ...manifest`` stays warning-free."""

    from .manifest import freeze_manifest as _freeze_manifest

    return _freeze_manifest(core)


def validate_wire_document(schema_filename: str, raw: bytes, **context: Any) -> Any:
    """Run the canonical semantic validator advertised by every public schema."""

    from .manifest import validate_wire_document as _validate_wire_document

    return _validate_wire_document(schema_filename, raw, **context)

__all__ = [
    "NO_WORKSPACE_CHECKPOINT_SHA256",
    "CampaignEventCore",
    "CandidateProposal",
    "CheckpointCore",
    "ExecutionPhaseOwner",
    "FactScope",
    "ImageBinding",
    "IssuedBudgetTicket",
    "MeasuredConfigurationId",
    "ModelRequest",
    "ModelResponse",
    "RequestClass",
    "RunManifest",
    "RunManifestCore",
    "SignedTaskLease",
    "SignedTaskLeaseCore",
    "SourceRef",
    "TicketTemplate",
    "TrustedFactCore",
    "TrustedRelationCore",
    "canonical_bytes",
    "canonical_sha256",
    "freeze_manifest",
    "parse_json_bytes",
    "validate_guest_artifact_path",
    "validate_canonical_utf8_base64",
    "validate_sha256_digest",
    "validate_task_lease_core",
    "validate_wire_document",
]
