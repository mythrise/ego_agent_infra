from __future__ import annotations

from typing import Any, Mapping, Sequence, Tuple

from pydantic import Field, field_validator

from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256
from benchmarks.secure_memory.models import Digest, StrictModel

from .contracts import AttentionFactRef, AttentionPacket


_DIGEST_PLACEHOLDER = "0" * 64


class AttentionBudgetExceeded(ValueError):
    """The mandatory task capsule cannot fit the requested conservative budget."""


class AttentionSourceContext(StrictModel):
    """Authoritative current-turn inputs accepted by the attention compiler.

    This type intentionally has no history, fact statement, or external-summary
    field. Retrieval supplies only already-filtered ``AttentionFactRef`` values.
    """

    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    turn: int = Field(ge=1, le=5)
    generation: int = Field(ge=1)
    requirement_ledger_sha256: Digest
    workspace_checkpoint_sha256: Digest
    policy_sha256: Digest
    memory_watermark: int = Field(ge=0)
    current_requirement_text: str = Field(min_length=1)
    unresolved_failure_ids: Tuple[str, ...] = ()
    unresolved_conflict_ids: Tuple[str, ...] = ()
    mandatory_policy_constraint_ids: Tuple[str, ...] = ()
    explicit_exclusion_digests: Tuple[Digest, ...] = ()

    @field_validator(
        "unresolved_failure_ids",
        "unresolved_conflict_ids",
        "mandatory_policy_constraint_ids",
        "explicit_exclusion_digests",
    )
    @classmethod
    def normalize_sorted_unique(
        cls, values: Tuple[str, ...], info: Any
    ) -> Tuple[str, ...]:
        if any(not value for value in values):
            raise ValueError(f"{info.field_name} values must be non-empty")
        return tuple(sorted(set(values)))


def _budget_payload(
    context: AttentionSourceContext,
    facts: Tuple[AttentionFactRef, ...],
    exclusions: Tuple[str, ...],
    token_budget: int,
) -> dict[str, Any]:
    return {
        "schema_version": "agentteams-attention-packet/v1",
        "tenant_id": context.tenant_id,
        "project_id": context.project_id,
        "task_id": context.task_id,
        "turn": context.turn,
        "generation": context.generation,
        "requirement_ledger_sha256": context.requirement_ledger_sha256,
        "workspace_checkpoint_sha256": context.workspace_checkpoint_sha256,
        "policy_sha256": context.policy_sha256,
        "memory_watermark": context.memory_watermark,
        "current_requirement_text": context.current_requirement_text,
        "unresolved_failure_ids": tuple(
            sorted(
                set(context.unresolved_failure_ids).union(
                    context.unresolved_conflict_ids
                )
            )
        ),
        "mandatory_policy_constraint_ids": context.mandatory_policy_constraint_ids,
        "eligible_fact_refs": facts,
        "explicit_exclusions": exclusions,
        "token_budget": token_budget,
    }


def _stable_canonical_byte_bound(payload: Mapping[str, Any]) -> int:
    """Return the fixed point for one token per canonical UTF-8 byte.

    The two digest placeholders have their final fixed width. Including the
    estimate field itself makes the bound explainable and byte-for-byte stable.
    """

    estimate = 0
    for _ in range(16):
        candidate = {
            **payload,
            "estimated_tokens": estimate,
            "source_context_sha256": _DIGEST_PLACEHOLDER,
            "packet_sha256": _DIGEST_PLACEHOLDER,
        }
        updated = len(canonical_bytes(candidate))
        if updated == estimate:
            return updated
        estimate = updated
    raise RuntimeError("attention token bound did not converge")


def _build_candidate(
    context: AttentionSourceContext,
    facts: Tuple[AttentionFactRef, ...],
    exclusions: Tuple[str, ...],
    token_budget: int,
) -> AttentionPacket:
    payload = _budget_payload(context, facts, exclusions, token_budget)
    estimated_tokens = _stable_canonical_byte_bound(payload)
    source_core = {**payload, "estimated_tokens": estimated_tokens}
    source_context_sha256 = canonical_sha256(
        "agentteams-attention-source-context", source_core
    )
    packet_core = {
        **source_core,
        "source_context_sha256": source_context_sha256,
    }
    return AttentionPacket(
        **packet_core,
        packet_sha256=canonical_sha256("agentteams-attention-packet", packet_core),
    )


def _validate_fact_scope(
    context: AttentionSourceContext, fact: AttentionFactRef
) -> None:
    if fact.tenant_id != context.tenant_id:
        raise ValueError("attention fact tenant does not match the current tenant")
    if fact.project_id != context.project_id:
        raise ValueError("attention fact project does not match the current project")
    if fact.evidence_watermark != context.memory_watermark:
        raise ValueError("attention fact has a stale evidence watermark")
    if fact.lifecycle != "VALIDATED":
        raise ValueError("attention fact lifecycle must be VALIDATED")


def build_attention_packet(
    context: AttentionSourceContext,
    fact_refs: Sequence[AttentionFactRef],
    *,
    token_budget: int,
) -> AttentionPacket:
    """Compile a deterministic, budget-bounded packet from current state and refs.

    The conservative estimate is one token per canonical UTF-8 byte. The
    compiler never reads history or accepts raw fact statements. Invalid-scope
    facts fail closed; valid facts are ranked by relevance then digest and only
    the lowest-ranked suffix may be removed.
    """

    if isinstance(token_budget, bool) or not isinstance(token_budget, int):
        raise ValueError("token_budget must be a positive integer")
    if token_budget <= 0:
        raise ValueError("token_budget must be a positive integer")

    facts = tuple(fact_refs)
    for fact in facts:
        if not isinstance(fact, AttentionFactRef):
            raise TypeError("fact_refs must contain only AttentionFactRef values")
        _validate_fact_scope(context, fact)

    fact_digests = tuple(fact.fact_sha256 for fact in facts)
    if len(set(fact_digests)) != len(fact_digests):
        raise ValueError("fact_refs contain a duplicate fact digest")

    configured_exclusions = set(context.explicit_exclusion_digests)
    if configured_exclusions.intersection(fact_digests):
        raise ValueError("an eligible fact cannot also be explicitly excluded")

    ranked = tuple(
        sorted(
            facts,
            key=lambda fact: (
                -fact.relevance_score_basis_points,
                fact.fact_sha256,
            ),
        )
    )
    for included_count in range(len(ranked), -1, -1):
        included = ranked[:included_count]
        omitted = {fact.fact_sha256 for fact in ranked[included_count:]}
        exclusions = tuple(sorted(configured_exclusions.union(omitted)))
        payload = _budget_payload(context, included, exclusions, token_budget)
        if _stable_canonical_byte_bound(payload) > token_budget:
            continue
        candidate = _build_candidate(context, included, exclusions, token_budget)
        return candidate

    raise AttentionBudgetExceeded(
        "mandatory attention capsule and explicit exclusions exceed token budget"
    )


def deterministic_conservative_token_bound(packet: AttentionPacket) -> int:
    """Return the packet's deterministic one-token-per-UTF-8-byte bound."""

    return len(canonical_bytes(packet))


__all__ = [
    "AttentionBudgetExceeded",
    "AttentionSourceContext",
    "build_attention_packet",
    "deterministic_conservative_token_bound",
]
