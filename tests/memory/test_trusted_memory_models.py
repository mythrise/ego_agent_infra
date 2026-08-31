from __future__ import annotations

import base64
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from apps.api.models import MemoryRecord
from apps.api.trusted_memory.models import (
    CandidateFact,
    CandidateProposal,
    ConflictGroup,
    ConflictMember,
    ConflictRecord,
    DecisionOutcome,
    FactProvenance,
    LegacyMemoryView,
    LifecycleTransition,
    LifecycleTransitionCore,
    MemoryOrigin,
    MemoryScope,
    MemoryState,
    RevocationRecord,
    RevocationRecordCore,
    SupersessionRecord,
    SupersessionRecordCore,
    TrustedFact,
    TrustedFactCore,
)
from benchmarks.secure_memory.canonical import canonical_sha256
from benchmarks.secure_memory.models import FactScope, SourceRef


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64


def _scope(**overrides: Any) -> MemoryScope:
    values: Dict[str, Any] = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "component": "apps.api",
        "version": "v1",
    }
    values.update(overrides)
    return MemoryScope(**values)


def _proposal(**overrides: Any) -> CandidateProposal:
    values: Dict[str, Any] = {
        "schema_version": "secure-memory-candidate/v1",
        "proposal_id": "candidate-001",
        "task_id": "task-001",
        "generation": 1,
        "claimed_fact_id": "fact-001",
        "statement_utf8_base64": base64.b64encode(b"tests passed").decode("ascii"),
        "memory_type": "procedural",
        "component": "apps.api",
        "outcome_claim": "KEEP",
        "applicability_scope": FactScope(
            tenant_id="tenant-a",
            project_id="project-a",
            component="apps.api",
            version="v1",
            problem_id="problem-001",
        ),
        "source_refs": (SourceRef(kind="evidence", identifier="evidence-001"),),
        "support_digest_claims": (DIGEST_A,),
    }
    values.update(overrides)
    return CandidateProposal(**values)


def _candidate_data(**overrides: Any) -> Dict[str, Any]:
    proposal = overrides.pop("proposal", _proposal())
    values: Dict[str, Any] = {
        "schema_version": "egoagentos-memory-candidate/v1",
        "candidate_id": "candidate-001",
        "lineage_id": "lineage-001",
        "revision": 1,
        "scope": _scope(),
        "outcome": DecisionOutcome.KEEP,
        "origin": MemoryOrigin.ORIGIN_UNVERIFIED,
        "state": MemoryState.CANDIDATE,
        "proposal": proposal,
        "proposal_digest": canonical_sha256("candidate-proposal", proposal),
    }
    values.update(overrides)
    return values


def _fact_core(**overrides: Any) -> TrustedFactCore:
    values: Dict[str, Any] = {
        "schema_version": "secure-memory-trusted-fact/v1",
        "fact_id": "fact-001",
        "fact_kind": "procedural",
        "statement_utf8_base64": base64.b64encode(b"tests passed").decode("ascii"),
        "outcome": "KEEP",
        "applicability_scope": FactScope(
            tenant_id="tenant-a",
            project_id="project-a",
            component="apps.api",
            version="v1",
            problem_id="problem-001",
        ),
        "source_refs": (SourceRef(kind="evidence", identifier="evidence-001"),),
        "support_digests": (DIGEST_A,),
    }
    values.update(overrides)
    return TrustedFactCore(**values)


def _provenance(core_digest: str, **overrides: Any) -> FactProvenance:
    values: Dict[str, Any] = {
        "schema_version": "egoagentos-fact-provenance/v1",
        "scope": _scope(),
        "task_id": "task-001",
        "generation": 1,
        "task_version": 4,
        "decision_id": "decision-001",
        "decision_digest": DIGEST_B,
        "decision_closure_digest": DIGEST_C,
        "origin": MemoryOrigin.LOCAL_TRUSTED,
        "evaluator_id": "sealed-evaluator",
        "evaluator_result_digest": DIGEST_D,
        "external_attestation_digest": None,
        "verified_fact_digests": (core_digest,),
        "evidence_ids": ("evidence-001", "evidence-002"),
        "evidence_digests": (DIGEST_A, DIGEST_E),
        "policy_version": "memory-policy-v1",
        "rule_version": "memory-rule-v1",
    }
    values.update(overrides)
    return FactProvenance(**values)


def _trusted_fact_data(**overrides: Any) -> Dict[str, Any]:
    core = overrides.pop("core", _fact_core())
    core_digest = canonical_sha256("trusted-fact", core)
    provenance = overrides.pop("provenance", _provenance(core_digest))
    values: Dict[str, Any] = {
        "schema_version": "egoagentos-trusted-memory-fact/v1",
        "revision_id": "revision-002",
        "lineage_id": "lineage-001",
        "revision": 2,
        "scope": _scope(),
        "outcome": DecisionOutcome.KEEP,
        "origin": MemoryOrigin.LOCAL_TRUSTED,
        "state": MemoryState.VALIDATED,
        "core": core,
        "trusted_fact_digest": core_digest,
        "provenance": provenance,
    }
    values.update(overrides)
    values["record_digest"] = canonical_sha256("trusted-memory-fact-record", values)
    return values


def test_candidate_is_strict_untrusted_and_cannot_self_promote() -> None:
    candidate = CandidateFact(**_candidate_data())

    assert candidate.state is MemoryState.CANDIDATE
    assert candidate.origin is MemoryOrigin.ORIGIN_UNVERIFIED
    with pytest.raises(ValidationError, match="CANDIDATE"):
        CandidateFact(**_candidate_data(state=MemoryState.VALIDATED))
    with pytest.raises(ValidationError, match="ORIGIN_UNVERIFIED|SYNTHETIC"):
        CandidateFact(**_candidate_data(origin=MemoryOrigin.LOCAL_TRUSTED))
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CandidateFact(**_candidate_data(decision_closure_digest=DIGEST_C))


def test_candidate_requires_exact_scope_outcome_and_domain_digest() -> None:
    proposal = _proposal()
    with pytest.raises(ValidationError, match="proposal_digest"):
        CandidateFact(**_candidate_data(proposal_digest=DIGEST_F))
    with pytest.raises(ValidationError, match="tenant|scope"):
        CandidateFact(**_candidate_data(scope=_scope(tenant_id="tenant-b")))
    with pytest.raises(ValidationError, match="outcome"):
        CandidateFact(**_candidate_data(outcome=DecisionOutcome.DROP))
    assert canonical_sha256("candidate-proposal", proposal) != canonical_sha256(
        "trusted-fact", proposal
    )


def test_evaluator_closed_fact_binds_exact_core_scope_outcome_and_provenance() -> None:
    fact = TrustedFact(**_trusted_fact_data())

    assert fact.provenance.evaluator_result_digest == DIGEST_D
    assert fact.trusted_fact_digest in fact.provenance.verified_fact_digests
    assert fact.origin is MemoryOrigin.LOCAL_TRUSTED
    with pytest.raises(ValidationError, match="frozen"):
        fact.revision = 3


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"trusted_fact_digest": DIGEST_F}, "trusted_fact_digest"),
        ({"scope": _scope(project_id="project-b")}, "project|scope"),
        ({"outcome": DecisionOutcome.DROP}, "outcome"),
        ({"record_digest": DIGEST_F}, "record_digest"),
    ],
)
def test_trusted_fact_rejects_mismatched_bindings(overrides: Dict[str, Any], message: str) -> None:
    values = _trusted_fact_data()
    values.update(overrides)
    if "record_digest" not in overrides:
        values["record_digest"] = canonical_sha256(
            "trusted-memory-fact-record",
            {key: value for key, value in values.items() if key != "record_digest"},
        )
    with pytest.raises(ValidationError, match=message):
        TrustedFact(**values)


def test_provenance_requires_sorted_unique_evidence_and_valid_origin_binding() -> None:
    core_digest = canonical_sha256("trusted-fact", _fact_core())
    with pytest.raises(ValidationError, match="sorted and duplicate-free"):
        _provenance(core_digest, evidence_ids=("evidence-002", "evidence-001"))
    with pytest.raises(ValidationError, match="sorted and duplicate-free"):
        _provenance(core_digest, verified_fact_digests=(core_digest, core_digest))
    with pytest.raises(ValidationError, match="evaluator"):
        _provenance(core_digest, evaluator_result_digest=None)
    with pytest.raises(ValidationError, match="external_attestation"):
        _provenance(
            core_digest,
            origin=MemoryOrigin.ATTESTED_EXTERNAL,
            evaluator_id=None,
            evaluator_result_digest=None,
            external_attestation_digest=None,
        )


def _transition_core(**overrides: Any) -> LifecycleTransitionCore:
    values: Dict[str, Any] = {
        "schema_version": "egoagentos-memory-lifecycle-transition/v1",
        "transition_id": "transition-001",
        "scope": _scope(),
        "lineage_id": "lineage-001",
        "fact_digest": DIGEST_A,
        "from_revision": 1,
        "to_revision": 2,
        "from_state": MemoryState.CANDIDATE,
        "to_state": MemoryState.VALIDATED,
        "actor_role": "validator",
        "actor_id": "memory-validator",
        "reason_code": "EVALUATOR_FACT_MATCHED",
        "decision_closure_digest": DIGEST_C,
    }
    values.update(overrides)
    return LifecycleTransitionCore(**values)


def test_lifecycle_is_monotonic_and_digest_bound() -> None:
    core = _transition_core()
    transition = LifecycleTransition(
        core=core,
        transition_digest=canonical_sha256("trusted-memory-lifecycle-transition", core),
    )
    assert transition.core.to_state is MemoryState.VALIDATED

    with pytest.raises(ValidationError, match="transition"):
        _transition_core(from_state=MemoryState.REJECTED, to_state=MemoryState.VALIDATED)
    with pytest.raises(ValidationError, match="next revision"):
        _transition_core(to_revision=3)
    with pytest.raises(ValidationError, match="transition_digest"):
        LifecycleTransition(core=core, transition_digest=DIGEST_F)


def _conflict_group(**overrides: Any) -> ConflictGroup:
    members = (
        ConflictMember(
            scope=_scope(),
            lineage_id="lineage-001",
            revision_id="revision-002",
            revision=2,
            fact_digest=DIGEST_A,
        ),
        ConflictMember(
            scope=_scope(),
            lineage_id="lineage-002",
            revision_id="revision-003",
            revision=3,
            fact_digest=DIGEST_B,
        ),
    )
    values: Dict[str, Any] = {
        "schema_version": "egoagentos-memory-conflict-group/v1",
        "conflict_group_id": "conflict-group-001",
        "scope": _scope(),
        "members": members,
        "reason_code": "CONTRADICTORY_EVALUATOR_FACTS",
        "decision_closure_digests": (DIGEST_C, DIGEST_D),
    }
    values.update(overrides)
    return ConflictGroup(**values)


def test_conflict_group_is_canonical_and_scope_closed() -> None:
    group = _conflict_group()
    record = ConflictRecord(
        group=group,
        conflict_digest=canonical_sha256("trusted-memory-conflict", group),
    )
    assert len(record.group.members) == 2

    with pytest.raises(ValidationError, match="canonically sorted"):
        _conflict_group(members=tuple(reversed(group.members)))
    cross_project = group.members[1].model_copy(update={"scope": _scope(project_id="project-b")})
    with pytest.raises(ValidationError, match="scope"):
        _conflict_group(members=(group.members[0], cross_project))
    with pytest.raises(ValidationError, match="conflict_digest"):
        ConflictRecord(group=group, conflict_digest=DIGEST_F)


def _supersession_core(**overrides: Any) -> SupersessionRecordCore:
    values: Dict[str, Any] = {
        "schema_version": "egoagentos-memory-supersession/v1",
        "supersession_id": "supersession-001",
        "scope": _scope(),
        "lineage_id": "lineage-001",
        "superseded_revision_id": "revision-001",
        "superseded_revision": 1,
        "superseding_revision_id": "revision-002",
        "superseding_revision": 2,
        "prior_revision_ids": (),
        "decision_closure_digest": DIGEST_C,
        "reason_code": "NEWER_EVALUATOR_FACT",
    }
    values.update(overrides)
    return SupersessionRecordCore(**values)


def test_supersession_rejects_self_cycles_and_stale_versions() -> None:
    core = _supersession_core()
    record = SupersessionRecord(
        core=core,
        supersession_digest=canonical_sha256("trusted-memory-supersession", core),
    )
    assert record.core.superseding_revision == 2

    with pytest.raises(ValidationError, match="itself"):
        _supersession_core(superseding_revision_id="revision-001")
    with pytest.raises(ValidationError, match="cycle"):
        _supersession_core(prior_revision_ids=("revision-002",))
    with pytest.raises(ValidationError, match="next revision"):
        _supersession_core(superseding_revision=4)
    with pytest.raises(ValidationError, match="supersession_digest"):
        SupersessionRecord(core=core, supersession_digest=DIGEST_F)


def _revocation_core(**overrides: Any) -> RevocationRecordCore:
    values: Dict[str, Any] = {
        "schema_version": "egoagentos-memory-revocation/v1",
        "revocation_id": "revocation-001",
        "scope": _scope(),
        "lineage_id": "lineage-001",
        "revision_id": "revision-002",
        "revision": 2,
        "expected_revision": 2,
        "fact_digest": DIGEST_A,
        "decision_closure_digest": DIGEST_C,
        "invalidating_evidence_ids": ("evidence-003", "evidence-004"),
        "invalidating_evidence_digests": (DIGEST_D, DIGEST_E),
        "reason_code": "SOURCE_REVOKED",
    }
    values.update(overrides)
    return RevocationRecordCore(**values)


def test_revocation_rejects_stale_or_nondeterministic_records() -> None:
    core = _revocation_core()
    record = RevocationRecord(
        core=core,
        revocation_digest=canonical_sha256("trusted-memory-revocation", core),
    )
    assert record.core.expected_revision == 2

    with pytest.raises(ValidationError, match="stale"):
        _revocation_core(expected_revision=1)
    with pytest.raises(ValidationError, match="sorted and duplicate-free"):
        _revocation_core(invalidating_evidence_ids=("evidence-004", "evidence-003"))
    with pytest.raises(ValidationError, match="revocation_digest"):
        RevocationRecord(core=core, revocation_digest=DIGEST_F)


def test_legacy_validated_rows_remain_origin_unverified() -> None:
    legacy = MemoryRecord(
        id="legacy-001",
        task_id="task-legacy",
        generation="1",
        memory_type="procedural",
        statement="legacy statement",
        component="apps.api",
        evidence_digest=DIGEST_A,
        review_id="review-legacy",
        validated=True,
        validated_by="legacy-validator",
    )
    view = LegacyMemoryView.from_memory_record(
        legacy,
        tenant_id="tenant-a",
        project_id="project-a",
        version="legacy-v1",
    )

    assert view.legacy_validated is True
    assert view.origin is MemoryOrigin.ORIGIN_UNVERIFIED
    assert view.state is MemoryState.CANDIDATE


@pytest.mark.parametrize(
    "forbidden",
    [
        {"approval": "yes"},
        {"capability": "workspace-write"},
        {"approval_token": "secret-token"},
        {"secret": "private"},
    ],
)
def test_memory_contract_rejects_authority_and_secret_fields(forbidden: Dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="approval|capability|token|secret|extra_forbidden"):
        CandidateFact(**_candidate_data(**forbidden))
