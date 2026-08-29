"""RXP — transport-independent executable experiment commitments."""

from .canonical import (
    canonical_bytes,
    canonical_json,
    digest_document,
    merkle_root,
    sha256_bytes,
)
from .demo import build_demo_ledger, demo_bytes
from .errors import RXPError
from .evidence import evidence_gate
from .grants import (
    GrantSigner,
    InMemoryReplayRegistry,
    SQLiteReplayRegistry,
    migrate_consumed_approval_v1,
)
from .ledger import MatrixLedger, verify_grant_signatures, verify_ledger_document
from .models import (
    ArtifactRef,
    Decision,
    DeterminismLevel,
    Evidence,
    GateAssessment,
    Grant,
    Intent,
    LegacyApprovalV1Binding,
    MatrixAxis,
    MatrixCellDefinition,
    MatrixLedgerDocument,
    MatrixPlan,
    Receipt,
    ResourceBounds,
    ResourceRequest,
    ResourceUsage,
    RunManifest,
)

__all__ = [
    "ArtifactRef",
    "Decision",
    "DeterminismLevel",
    "Evidence",
    "GateAssessment",
    "Grant",
    "GrantSigner",
    "InMemoryReplayRegistry",
    "Intent",
    "LegacyApprovalV1Binding",
    "MatrixAxis",
    "MatrixCellDefinition",
    "MatrixLedger",
    "MatrixLedgerDocument",
    "MatrixPlan",
    "Receipt",
    "RXPError",
    "ResourceBounds",
    "ResourceRequest",
    "ResourceUsage",
    "RunManifest",
    "SQLiteReplayRegistry",
    "build_demo_ledger",
    "canonical_bytes",
    "canonical_json",
    "digest_document",
    "demo_bytes",
    "evidence_gate",
    "migrate_consumed_approval_v1",
    "merkle_root",
    "sha256_bytes",
    "verify_grant_signatures",
    "verify_ledger_document",
]
