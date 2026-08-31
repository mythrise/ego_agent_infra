"""Frozen decision closures built only from Control and verified evaluator inputs."""

from __future__ import annotations

from typing import Literal, Sequence, Tuple

from pydantic import Field, field_validator, model_validator

from benchmarks.secure_memory.canonical import canonical_sha256
from benchmarks.secure_memory.models import Digest, StrictModel
from benchmarks.secure_memory.substrate.admission import AdmissionReceipt, AdmissionStatus
from benchmarks.secure_memory.substrate.evaluator_channel import EvaluatorSourceReceipt

from .models import DecisionOutcome


class ClosureRejected(ValueError):
    """Raised when an input lacks the authority or exact binding needed to close."""


def _sorted_unique(values: Tuple[str, ...], name: str) -> Tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{name} must be sorted and duplicate-free")
    return values


class EvidenceBinding(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=200)
    evidence_digest: Digest


class TerminalDecisionRecord(StrictModel):
    schema_version: Literal["egoagentos-terminal-decision/v1"]
    tenant_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)
    generation: int = Field(ge=1)
    task_version: int = Field(ge=1)
    decision_id: str = Field(min_length=1, max_length=200)
    outcome: DecisionOutcome
    terminal: bool
    authority_source: Literal["CONTROL", "MODEL", "WORKER", "MATRIX", "REVIEWER"]
    decision_digest: Digest

    @model_validator(mode="after")
    def validate_digest(self) -> "TerminalDecisionRecord":
        core = self.model_dump(mode="python", exclude={"decision_digest"})
        if self.decision_digest != canonical_sha256("trusted-memory-terminal-decision", core):
            raise ValueError("terminal decision digest mismatch")
        return self


class EvaluatorResultBinding(StrictModel):
    schema_version: Literal["egoagentos-evaluator-result-binding/v1"]
    evaluator_id: str = Field(min_length=1, max_length=200)
    source_receipt: EvaluatorSourceReceipt
    evaluator_result_digest: Digest
    signature_verified: bool
    verified_fact_digests: Tuple[Digest, ...] = Field(min_length=1)

    @field_validator("verified_fact_digests")
    @classmethod
    def validate_fact_digests(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _sorted_unique(values, "verified_fact_digests")


class DecisionClosureCore(StrictModel):
    schema_version: Literal["egoagentos-decision-closure/v1"]
    closure_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)
    generation: int = Field(ge=1)
    task_version: int = Field(ge=1)
    decision_id: str = Field(min_length=1, max_length=200)
    decision_digest: Digest
    decision_outcome: DecisionOutcome
    evaluator_id: str = Field(min_length=1, max_length=200)
    evaluator_source_receipt_digest: Digest
    evaluator_result_digest: Digest
    verified_fact_digests: Tuple[Digest, ...] = Field(min_length=1)
    evidence_ids: Tuple[str, ...] = Field(min_length=1)
    evidence_digests: Tuple[Digest, ...] = Field(min_length=1)
    evidence_bindings_digest: Digest
    admission_receipt_digests: Tuple[Digest, ...] = Field(min_length=1)
    policy_version: str = Field(min_length=1, max_length=120)
    rule_version: str = Field(min_length=1, max_length=120)
    memory_watermark: int = Field(ge=0)

    @field_validator(
        "verified_fact_digests",
        "evidence_ids",
        "evidence_digests",
        "admission_receipt_digests",
    )
    @classmethod
    def validate_sets(cls, values: Tuple[str, ...], info: object) -> Tuple[str, ...]:
        return _sorted_unique(values, getattr(info, "field_name"))

    @model_validator(mode="after")
    def validate_evidence_alignment(self) -> "DecisionClosureCore":
        if len(self.evidence_ids) != len(self.evidence_digests):
            raise ValueError("evidence IDs and digests must align")
        return self


class DecisionClosure(StrictModel):
    core: DecisionClosureCore
    closure_digest: Digest

    @model_validator(mode="after")
    def validate_digest(self) -> "DecisionClosure":
        expected = canonical_sha256("trusted-memory-decision-closure", self.core)
        if self.closure_digest != expected:
            raise ValueError("decision closure digest mismatch")
        return self


def build_decision_closure(
    *,
    terminal_decision: TerminalDecisionRecord,
    evaluator_result: EvaluatorResultBinding,
    evidence: Sequence[EvidenceBinding],
    admission_receipts: Sequence[AdmissionReceipt],
    policy_version: str,
    rule_version: str,
    memory_watermark: int,
) -> DecisionClosure:
    if terminal_decision.authority_source != "CONTROL":
        raise ClosureRejected("control_authority_required")
    if not terminal_decision.terminal:
        raise ClosureRejected("terminal_decision_required")
    if evaluator_result.signature_verified is not True:
        raise ClosureRejected("evaluator_signature_not_verified")
    source = evaluator_result.source_receipt
    if not source.source_verified or source.admission.status is not AdmissionStatus.ADMITTED:
        raise ClosureRejected("evaluator_source_not_admitted")
    if (
        source.envelope_sha256 is None
        or evaluator_result.evaluator_result_digest != source.envelope_sha256
    ):
        raise ClosureRejected("evaluator_result_digest_mismatch")
    if (
        source.task_id != terminal_decision.task_id
        or source.generation != terminal_decision.generation
    ):
        raise ClosureRejected("evaluator_scope_mismatch")
    if not admission_receipts or any(
        receipt.status is not AdmissionStatus.ADMITTED for receipt in admission_receipts
    ):
        raise ClosureRejected("admission_receipt_not_admitted")
    if any(
        receipt.task_id != terminal_decision.task_id
        or receipt.generation != terminal_decision.generation
        for receipt in admission_receipts
    ):
        raise ClosureRejected("admission_scope_mismatch")
    if not evidence:
        raise ClosureRejected("evidence_required")

    evidence_sorted = tuple(
        sorted(evidence, key=lambda item: (item.evidence_id, item.evidence_digest))
    )
    evidence_ids = tuple(item.evidence_id for item in evidence_sorted)
    evidence_digests = tuple(sorted(item.evidence_digest for item in evidence_sorted))
    if len(set(evidence_ids)) != len(evidence_ids) or len(set(evidence_digests)) != len(
        evidence_digests
    ):
        raise ClosureRejected("duplicate_evidence")
    receipt_digests = tuple(sorted(receipt.receipt_sha256 for receipt in admission_receipts))
    identity = canonical_sha256(
        "trusted-memory-decision-closure-id",
        {
            "decision_digest": terminal_decision.decision_digest,
            "evaluator_result_digest": evaluator_result.evaluator_result_digest,
            "source_receipt_digest": source.receipt_sha256,
        },
    )
    core = DecisionClosureCore(
        schema_version="egoagentos-decision-closure/v1",
        closure_id=f"closure-{identity[:32]}",
        tenant_id=terminal_decision.tenant_id,
        project_id=terminal_decision.project_id,
        task_id=terminal_decision.task_id,
        generation=terminal_decision.generation,
        task_version=terminal_decision.task_version,
        decision_id=terminal_decision.decision_id,
        decision_digest=terminal_decision.decision_digest,
        decision_outcome=terminal_decision.outcome,
        evaluator_id=evaluator_result.evaluator_id,
        evaluator_source_receipt_digest=source.receipt_sha256,
        evaluator_result_digest=evaluator_result.evaluator_result_digest,
        verified_fact_digests=evaluator_result.verified_fact_digests,
        evidence_ids=evidence_ids,
        evidence_digests=evidence_digests,
        evidence_bindings_digest=canonical_sha256(
            "trusted-memory-evidence-bindings", evidence_sorted
        ),
        admission_receipt_digests=receipt_digests,
        policy_version=policy_version,
        rule_version=rule_version,
        memory_watermark=memory_watermark,
    )
    return DecisionClosure(
        core=core,
        closure_digest=canonical_sha256("trusted-memory-decision-closure", core),
    )


__all__ = [
    "ClosureRejected",
    "DecisionClosure",
    "DecisionClosureCore",
    "EvaluatorResultBinding",
    "EvidenceBinding",
    "TerminalDecisionRecord",
    "build_decision_closure",
]
