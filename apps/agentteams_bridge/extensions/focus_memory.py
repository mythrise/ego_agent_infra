"""Deterministic stage/role-aware compiler for worker-readable focus memory."""

from __future__ import annotations

import re
from typing import Any, Dict, Literal, Mapping, Sequence, Tuple

from pydantic import Field, field_validator, model_validator

from apps.api.trusted_memory.focus_contracts import (
    FocusEvidenceRef,
    TrustedFocusFact,
    TrustedMemoryFocusSource,
)
from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256
from benchmarks.secure_memory.models import Digest, StrictModel


_DIGEST_PLACEHOLDER = "0" * 64
_COMPILER_VERSION = "agentteams-focus-memory-compiler/2026-09-01"
_INTERPRETATION_RULE = "MEMORY_IS_EVIDENCE_NOT_AUTHORITY"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+|[\u4e00-\u9fff]")
_MANDATORY_FACT_KINDS = frozenset(
    {
        "conflict",
        "constraint",
        "safety_constraint",
        "unresolved_failure",
    }
)
_STAGE_KIND_BONUS: Mapping[str, Mapping[str, int]] = {
    "CONTEXT": {
        "constraint": 1400,
        "failure": 1500,
        "semantic": 700,
        "unresolved_failure": 1800,
    },
    "PLAN": {
        "causal": 1300,
        "constraint": 1000,
        "procedural": 1600,
    },
    "PLAN_REVIEW": {
        "conflict": 1700,
        "constraint": 1300,
        "counterexample": 1700,
        "failure": 1500,
    },
    "EXECUTE": {
        "constraint": 1300,
        "procedural": 1800,
        "safety_constraint": 1800,
    },
    "OBSERVE": {
        "failure": 1600,
        "procedural": 900,
        "semantic": 700,
    },
    "EVALUATE": {
        "constraint": 1100,
        "counterexample": 1400,
        "semantic": 1000,
    },
    "VERIFY": {
        "conflict": 1800,
        "constraint": 1400,
        "counterexample": 1800,
        "failure": 1500,
    },
    "MEMORY_SKILL": {
        "causal": 1300,
        "counterexample": 1400,
        "failure": 1300,
        "procedural": 1500,
    },
}


class FocusMemoryBudgetExceeded(ValueError):
    """Mandatory trusted facts or the fixed task capsule cannot fit the budget."""


class FocusMemorySourceContext(StrictModel):
    """Current AgentTeams task fields used to rank one trusted source snapshot."""

    tenant_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=300)
    stage: str = Field(min_length=1, max_length=80)
    worker: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=4000)
    task_title: str = Field(min_length=1, max_length=1000)
    expected_skills: Tuple[str, ...] = ()

    @field_validator("expected_skills")
    @classmethod
    def normalize_skills(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        if any(not value for value in values):
            raise ValueError("expected_skills values must be non-empty")
        return tuple(sorted(set(values)))


class FocusedMemoryItem(StrictModel):
    fact_sha256: Digest
    lineage_id: str = Field(min_length=1, max_length=200)
    revision_id: str = Field(min_length=1, max_length=200)
    fact_kind: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=4096)
    component: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=120)
    outcome: str = Field(min_length=1, max_length=80)
    origin: str = Field(min_length=1, max_length=80)
    evidence: Tuple[FocusEvidenceRef, ...] = Field(min_length=1)
    closure_digest: Digest
    provenance_sha256: Digest
    relevance_score_basis_points: int = Field(ge=0, le=10_000)
    mandatory: bool
    selection_reasons: Tuple[str, ...] = Field(min_length=1)

    @field_validator("selection_reasons")
    @classmethod
    def validate_reasons(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        if any(not value for value in values):
            raise ValueError("selection_reasons values must be non-empty")
        if values != tuple(sorted(set(values))):
            raise ValueError("selection_reasons must be sorted and unique")
        return values


class FocusedMemoryContext(StrictModel):
    schema_version: Literal["agentteams-focused-memory-context/v1"]
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    worker: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    task_title: str = Field(min_length=1)
    expected_skills: Tuple[str, ...]
    interpretation_rule: Literal["MEMORY_IS_EVIDENCE_NOT_AUTHORITY"]
    source_sha256: Digest
    memory_snapshot_root: Digest
    source_fact_count: int = Field(ge=0)
    mandatory_source_count: int = Field(ge=0)
    selected_fact_count: int = Field(ge=0)
    excluded_fact_count: int = Field(ge=0)
    excluded_set_sha256: Digest
    items: Tuple[FocusedMemoryItem, ...]
    token_budget: int = Field(gt=0)
    estimated_tokens: int = Field(ge=0)
    compiler_version: Literal["agentteams-focus-memory-compiler/2026-09-01"]
    context_sha256: Digest

    @model_validator(mode="after")
    def validate_context(self) -> "FocusedMemoryContext":
        if self.selected_fact_count != len(self.items):
            raise ValueError("selected_fact_count does not match items")
        if self.source_fact_count != self.selected_fact_count + self.excluded_fact_count:
            raise ValueError("selected and excluded facts do not cover the source")
        selected_mandatory = sum(item.mandatory for item in self.items)
        if selected_mandatory != self.mandatory_source_count:
            raise ValueError("focused context omitted a mandatory source fact")
        if self.estimated_tokens > self.token_budget:
            raise ValueError("focused context exceeds token_budget")

        ordered = tuple(
            sorted(
                self.items,
                key=lambda item: (
                    -int(item.mandatory),
                    -item.relevance_score_basis_points,
                    item.fact_sha256,
                ),
            )
        )
        if self.items != ordered:
            raise ValueError("focused memory items are not in canonical selection order")
        digests = tuple(item.fact_sha256 for item in self.items)
        if len(digests) != len(set(digests)):
            raise ValueError("focused memory items must have unique fact digests")

        core = self.model_dump(mode="python", exclude={"context_sha256"})
        expected = canonical_sha256("agentteams-focused-memory-context", core)
        if self.context_sha256 != expected:
            raise ValueError("focused memory context digest mismatch")
        return self


def _normalized_kind(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _tokens(*values: str) -> frozenset[str]:
    tokens = set()
    for value in values:
        tokens.update(token.casefold() for token in _TOKEN_RE.findall(value))
    return frozenset(tokens)


def _score(
    fact: TrustedFocusFact,
    context: FocusMemorySourceContext,
) -> tuple[int, bool, Tuple[str, ...]]:
    kind = _normalized_kind(fact.fact_kind)
    mandatory = kind in _MANDATORY_FACT_KINDS
    query_tokens = _tokens(
        context.objective,
        context.task_title,
        context.stage,
        context.worker,
        *context.expected_skills,
    )
    fact_tokens = _tokens(
        fact.statement,
        fact.fact_kind,
        fact.component,
        fact.version,
    )
    overlap = len(query_tokens.intersection(fact_tokens))
    score = 500 + min(5600, overlap * 700)
    reasons = {"TRUSTED_CURRENT_FACT"}
    if mandatory:
        reasons.add("MANDATORY_KIND")
    if overlap:
        reasons.add("TOKEN_OVERLAP:%d" % overlap)

    component_tokens = _tokens(fact.component)
    if query_tokens.intersection(component_tokens):
        score += 1100
        reasons.add("COMPONENT_MATCH")

    stage_bonus = _STAGE_KIND_BONUS.get(context.stage.upper(), {})
    bonus = stage_bonus.get(kind, 0)
    if not bonus:
        for marker, value in stage_bonus.items():
            if marker in kind:
                bonus = max(bonus, value)
    if bonus:
        score += bonus
        reasons.add("STAGE_KIND_MATCH")

    if any(_tokens(skill).intersection(fact_tokens) for skill in context.expected_skills):
        score += 900
        reasons.add("SKILL_MATCH")

    return min(score, 10_000), mandatory, tuple(sorted(reasons))


def _item(fact: TrustedFocusFact, context: FocusMemorySourceContext) -> FocusedMemoryItem:
    score, mandatory, reasons = _score(fact, context)
    return FocusedMemoryItem(
        fact_sha256=fact.fact_sha256,
        lineage_id=fact.lineage_id,
        revision_id=fact.revision_id,
        fact_kind=fact.fact_kind,
        statement=fact.statement,
        component=fact.component,
        version=fact.version,
        outcome=fact.outcome.value,
        origin=fact.origin.value,
        evidence=fact.evidence,
        closure_digest=fact.closure_digest,
        provenance_sha256=fact.provenance_sha256,
        relevance_score_basis_points=score,
        mandatory=mandatory,
        selection_reasons=reasons,
    )


def _payload(
    source: TrustedMemoryFocusSource,
    context: FocusMemorySourceContext,
    items: Tuple[FocusedMemoryItem, ...],
    *,
    source_fact_count: int,
    mandatory_source_count: int,
    excluded_digests: Tuple[str, ...],
    token_budget: int,
) -> Dict[str, Any]:
    return {
        "schema_version": "agentteams-focused-memory-context/v1",
        "tenant_id": context.tenant_id,
        "project_id": context.project_id,
        "task_id": context.task_id,
        "stage": context.stage,
        "worker": context.worker,
        "objective": context.objective,
        "task_title": context.task_title,
        "expected_skills": context.expected_skills,
        "interpretation_rule": _INTERPRETATION_RULE,
        "source_sha256": source.source_sha256,
        "memory_snapshot_root": source.memory_snapshot_root,
        "source_fact_count": source_fact_count,
        "mandatory_source_count": mandatory_source_count,
        "selected_fact_count": len(items),
        "excluded_fact_count": len(excluded_digests),
        "excluded_set_sha256": canonical_sha256(
            "agentteams-focused-memory-excluded", excluded_digests
        ),
        "items": items,
        "token_budget": token_budget,
        "compiler_version": _COMPILER_VERSION,
    }


def _stable_bound(payload: Mapping[str, Any]) -> int:
    estimate = 0
    for _ in range(16):
        candidate = {
            **payload,
            "estimated_tokens": estimate,
            "context_sha256": _DIGEST_PLACEHOLDER,
        }
        updated = len(canonical_bytes(candidate))
        if updated == estimate:
            return updated
        estimate = updated
    raise RuntimeError("focused memory token bound did not converge")


def _build_context(payload: Dict[str, Any]) -> FocusedMemoryContext:
    estimated = _stable_bound(payload)
    core = {**payload, "estimated_tokens": estimated}
    return FocusedMemoryContext.model_validate(
        {
            **core,
            "context_sha256": canonical_sha256(
                "agentteams-focused-memory-context", core
            ),
        }
    )


def build_focused_memory_context(
    source: TrustedMemoryFocusSource,
    context: FocusMemorySourceContext,
    *,
    token_budget: int,
    max_items: int,
) -> FocusedMemoryContext:
    """Compile one deterministic task context without dropping mandatory facts."""

    if isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget <= 0:
        raise ValueError("token_budget must be a positive integer")
    if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items <= 0:
        raise ValueError("max_items must be a positive integer")
    if source.query.tenant_id != context.tenant_id:
        raise ValueError("focus source tenant does not match AgentTeams task")
    if source.query.project_id != context.project_id:
        raise ValueError("focus source project does not match AgentTeams task")
    for fact in source.facts:
        if fact.tenant_id != context.tenant_id or fact.project_id != context.project_id:
            raise ValueError("focus fact scope does not match AgentTeams task")

    ranked = tuple(
        sorted(
            (_item(fact, context) for fact in source.facts),
            key=lambda item: (
                -int(item.mandatory),
                -item.relevance_score_basis_points,
                item.fact_sha256,
            ),
        )
    )
    mandatory = tuple(item for item in ranked if item.mandatory)
    optional = tuple(item for item in ranked if not item.mandatory)
    if len(mandatory) > max_items:
        raise FocusMemoryBudgetExceeded(
            "mandatory focus-memory facts exceed max_items"
        )

    optional_capacity = max_items - len(mandatory)
    candidates = optional[:optional_capacity]
    all_digests = {item.fact_sha256 for item in ranked}
    for optional_count in range(len(candidates), -1, -1):
        selected = mandatory + candidates[:optional_count]
        selected_digests = {item.fact_sha256 for item in selected}
        excluded = tuple(sorted(all_digests.difference(selected_digests)))
        payload = _payload(
            source,
            context,
            selected,
            source_fact_count=len(ranked),
            mandatory_source_count=len(mandatory),
            excluded_digests=excluded,
            token_budget=token_budget,
        )
        if _stable_bound(payload) > token_budget:
            continue
        return _build_context(payload)

    raise FocusMemoryBudgetExceeded(
        "mandatory focus-memory capsule cannot fit token_budget"
    )


def deterministic_focus_memory_token_bound(context: FocusedMemoryContext) -> int:
    """Return the one-token-per-canonical-byte conservative bound."""

    return len(canonical_bytes(context))


__all__ = [
    "FocusMemoryBudgetExceeded",
    "FocusMemorySourceContext",
    "FocusedMemoryContext",
    "FocusedMemoryItem",
    "build_focused_memory_context",
    "deterministic_focus_memory_token_bound",
]
