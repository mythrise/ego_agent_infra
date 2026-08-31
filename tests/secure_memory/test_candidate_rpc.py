"""Candidate RPC validation and immutable quota regression tests."""

import base64

import pytest

from benchmarks.secure_memory.substrate.candidate_rpc import (
    CandidateQuotaLedger,
    CandidateRejected,
    CandidateRpc,
)


def _proposal(**overrides: object) -> dict[str, object]:
    proposal = {
        "schema_version": "secure-memory-candidate/v1",
        "proposal_id": "p-1",
        "task_id": "task-1",
        "generation": 1,
        "claimed_fact_id": None,
        "statement_utf8_base64": base64.b64encode(b"a supported candidate").decode(),
        "memory_type": "semantic",
        "component": "planner",
        "outcome_claim": "KEEP",
        "applicability_scope": {"project_id": "project-1", "component": "planner"},
        "source_refs": [],
        "support_digest_claims": [],
    }
    proposal.update(overrides)
    return proposal


def _rpc() -> CandidateRpc:
    return CandidateRpc(
        ledger=CandidateQuotaLedger(),
        campaign_id="campaign-1",
        arm="A",
        tenant="tenant-1",
    )


def test_canonical_duplicate_is_idempotent_but_cross_arm_and_tenant_fail() -> None:
    rpc = _rpc()
    proposal = _proposal()
    receipt = rpc.propose(proposal, turn_id="turn-1", arm="A", tenant="tenant-1")
    assert rpc.propose(proposal, turn_id="turn-1", arm="A", tenant="tenant-1") == receipt
    with pytest.raises(CandidateRejected, match="arm_mismatch"):
        rpc.propose(proposal, turn_id="turn-1", arm="B", tenant="tenant-1")
    with pytest.raises(CandidateRejected, match="tenant_mismatch"):
        rpc.propose(proposal, turn_id="turn-1", arm="A", tenant="other")


@pytest.mark.parametrize("key", ["Gate", "decision", "origin", "validator", "tenant_id"])
def test_forbidden_trust_and_tenant_fields_are_recursive(key: str) -> None:
    proposal = _proposal(component={"nested": {key: "forged"}})
    with pytest.raises(CandidateRejected, match="forbidden_field"):
        _rpc().propose(proposal, turn_id="turn-1", arm="A", tenant="tenant-1")


def test_size_refs_and_immutable_quotas_cannot_be_regained_by_retry() -> None:
    rpc = _rpc()
    with pytest.raises(CandidateRejected, match="statement_too_long"):
        rpc.propose(_proposal(statement_utf8_base64=base64.b64encode(b"x" * 2049).decode()), turn_id="t", arm="A", tenant="tenant-1")
    with pytest.raises(CandidateRejected, match="too_many_source_refs"):
        rpc.propose(_proposal(source_refs=[{"kind": "test", "identifier": str(n)} for n in range(17)]), turn_id="t", arm="A", tenant="tenant-1")
    for number in range(16):
        rpc.propose(_proposal(proposal_id=f"turn-{number}"), turn_id="turn", arm="A", tenant="tenant-1")
    with pytest.raises(CandidateRejected, match="turn_quota_exhausted"):
        rpc.propose(_proposal(proposal_id="blocked"), turn_id="turn", arm="A", tenant="tenant-1")
    with pytest.raises(CandidateRejected, match="turn_quota_exhausted"):
        rpc.propose(_proposal(proposal_id="retry"), turn_id="turn", arm="A", tenant="tenant-1")


def test_problem_campaign_queue_and_rate_limits() -> None:
    rpc = _rpc()
    for number in range(32):
        rpc.propose(_proposal(proposal_id=f"q-{number}", task_id="problem"), turn_id=f"turn-{number}", arm="A", tenant="tenant-1")
    with pytest.raises(CandidateRejected, match="problem_quota_exhausted"):
        rpc.propose(_proposal(proposal_id="q-over", task_id="problem"), turn_id="another", arm="A", tenant="tenant-1")
    assert rpc.ledger.queue("campaign-1") == 32
    with pytest.raises(CandidateRejected, match="queue_depth_exhausted"):
        rpc.propose(_proposal(proposal_id="queue-over", task_id="another"), turn_id="another", arm="A", tenant="tenant-1")


def test_campaign_quota_is_immutable_after_each_frame_leaves_the_queue() -> None:
    rpc = _rpc()
    for number in range(128):
        rpc.propose(
            _proposal(proposal_id=f"campaign-{number}", task_id=f"problem-{number}"),
            turn_id="turn-1",
            arm="A",
            tenant="tenant-1",
        )
        rpc.ledger.complete("campaign-1")
    with pytest.raises(CandidateRejected, match="campaign_quota_exhausted"):
        rpc.propose(
            _proposal(proposal_id="campaign-over", task_id="fresh-problem"),
            turn_id="turn-1",
            arm="A",
            tenant="tenant-1",
        )


def test_schema_rejection_consumes_an_opportunity_so_retry_cannot_regain_it() -> None:
    rpc = _rpc()
    invalid = _proposal(statement_utf8_base64="not-base64")
    for number in range(16):
        invalid["proposal_id"] = f"invalid-{number}"
        with pytest.raises(CandidateRejected, match="schema_invalid"):
            rpc.propose(invalid, turn_id="turn-1", arm="A", tenant="tenant-1")
    with pytest.raises(CandidateRejected, match="turn_quota_exhausted"):
        rpc.propose(_proposal(proposal_id="valid-retry"), turn_id="turn-1", arm="A", tenant="tenant-1")
