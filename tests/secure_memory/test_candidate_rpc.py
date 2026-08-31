"""Trusted-context candidate RPC regression tests."""

import base64
import json
import threading

import pytest

from benchmarks.secure_memory.substrate.candidate_rpc import (
    CandidateContext,
    CandidateQuotaLedger,
    CandidateRejected,
    CandidateRpc,
)


def _proposal(**overrides: object) -> dict[str, object]:
    result = {
        "schema_version": "secure-memory-candidate/v1",
        "proposal_id": "p-1",
        "task_id": "task-1",
        "generation": 1,
        "claimed_fact_id": None,
        "statement_utf8_base64": base64.b64encode(b"candidate").decode(),
        "memory_type": "semantic",
        "component": "planner",
        "outcome_claim": "KEEP",
        "applicability_scope": {"project_id": "project-1", "component": "planner"},
        "source_refs": [],
        "support_digest_claims": [],
    }
    result.update(overrides)
    return result


def _context(**overrides: object) -> CandidateContext:
    result = dict(
        campaign_id="campaign-1",
        configuration_id="A",
        tenant="tenant-1",
        problem_id="problem-1",
        task_id="task-1",
        generation=1,
        turn="turn-1",
        attempt="attempt-1",
        idempotency_key="idem-1",
    )
    result.update(overrides)
    return CandidateContext(**result)


def _rpc(clock=None) -> CandidateRpc:
    return CandidateRpc(ledger=CandidateQuotaLedger(monotonic=clock), monotonic=clock)


def _receipt_id(receipt: bytes) -> str:
    return str(json.loads(receipt)["receipt_id"])


def test_guest_task_generation_and_cross_boundary_duplicate_fail_closed() -> None:
    rpc = _rpc()
    context = _context()
    proposal = _proposal()
    receipt = rpc.propose(proposal, context=context)
    assert rpc.propose(proposal, context=context) == receipt
    with pytest.raises(CandidateRejected, match="task_mismatch"):
        rpc.propose(
            _proposal(task_id="forged", proposal_id="forged"),
            context=_context(attempt="forged", idempotency_key="forged"),
        )
    with pytest.raises(CandidateRejected, match="cross_boundary"):
        rpc.propose(
            proposal, context=_context(turn="turn-2", attempt="attempt-2", idempotency_key="idem-2")
        )


def test_malformed_and_forbidden_attempts_consume_before_schema_and_do_not_queue() -> None:
    rpc = _rpc()
    context = _context()
    for number in range(16):
        with pytest.raises(CandidateRejected):
            rpc.propose(
                _proposal(proposal_id="bad-%s" % number, component={"nested": {"Gate": "x"}}),
                context=_context(attempt="a-%s" % number, idempotency_key="i-%s" % number),
            )
    with pytest.raises(CandidateRejected, match="turn_quota_exhausted"):
        rpc.propose(
            _proposal(proposal_id="retry"),
            context=_context(attempt="a-final", idempotency_key="i-final"),
        )
    assert rpc.ledger.queue(context) == 0


def test_rate_limits_consume_opportunity_without_queue() -> None:
    now = [0.0]
    rpc = _rpc(clock=lambda: now[0])
    for number in range(16):
        context = _context(
            problem_id="problem-%s" % number,
            turn="turn-%s" % number,
            attempt="a%s" % number,
            idempotency_key="i%s" % number,
        )
        receipt = rpc.propose(_proposal(proposal_id="burst-%s" % number), context=context)
        rpc.complete(context=context, receipt_id=_receipt_id(receipt))
    with pytest.raises(CandidateRejected, match="burst_rate_exhausted"):
        rpc.propose(
            _proposal(proposal_id="burst-over"),
            context=_context(turn="turn-over", attempt="ab", idempotency_key="ib"),
        )
    assert rpc.ledger.queue(_context()) == 0


def test_rolling_rate_limit_is_frozen_at_32_attempts_per_minute() -> None:
    now = [0.0]
    rpc = _rpc(clock=lambda: now[0])
    for number in range(32):
        context = _context(
            problem_id="rolling-problem-%s" % number,
            turn="turn-%s" % number,
            attempt="a%s" % number,
            idempotency_key="i%s" % number,
        )
        receipt = rpc.propose(_proposal(proposal_id="rolling-%s" % number), context=context)
        rpc.complete(context=context, receipt_id=_receipt_id(receipt))
        now[0] += 1.1
    with pytest.raises(CandidateRejected, match="rolling_rate_exhausted"):
        rpc.propose(
            _proposal(proposal_id="rolling-over"),
            context=_context(
                problem_id="problem-overflow",
                turn="overflow",
                attempt="overflow",
                idempotency_key="overflow",
            ),
        )


def test_malformed_retry_also_consumes_a_trusted_attempt() -> None:
    rpc = _rpc()
    for number in range(16):
        with pytest.raises(CandidateRejected, match="schema_invalid"):
            rpc.propose(
                "not-a-mapping",
                context=_context(attempt="bad-%s" % number, idempotency_key="bad-%s" % number),
            )  # type: ignore[arg-type]
    with pytest.raises(CandidateRejected, match="turn_quota_exhausted"):
        rpc.propose(
            _proposal(proposal_id="after-malformed"),
            context=_context(attempt="late", idempotency_key="late"),
        )


def test_concurrent_duplicate_and_bound_queue_completion() -> None:
    rpc = _rpc()
    context = _context()
    proposal = _proposal()
    result = []
    threads = [
        threading.Thread(target=lambda: result.append(rpc.propose(proposal, context=context)))
        for _ in range(2)
    ]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert result[0] == result[1]
    receipt_id = _receipt_id(result[0])
    rpc.complete(context=context, receipt_id=receipt_id)
    rpc.complete(context=context, receipt_id=receipt_id)
    with pytest.raises(CandidateRejected, match="queue_completion"):
        rpc.complete(context=context, receipt_id="forged")


def test_semantic_quotas_and_terminal_retries_are_shared_across_handlers() -> None:
    ledger = CandidateQuotaLedger()
    for number in range(16):
        context = _context(
            task_id="task-%s" % number,
            generation=number + 1,
            attempt="a-%s" % number,
            idempotency_key="i-%s" % number,
        )
        CandidateRpc(ledger=ledger).propose(
            _proposal(
                proposal_id="p-%s" % number, task_id="task-%s" % number, generation=number + 1
            ),
            context=context,
        )
    with pytest.raises(CandidateRejected, match="turn_quota_exhausted"):
        CandidateRpc(ledger=ledger).propose(
            _proposal(proposal_id="over", task_id="new", generation=99),
            context=_context(task_id="new", generation=99, attempt="over", idempotency_key="over"),
        )
    bad = _context(attempt="bad", idempotency_key="bad")
    with pytest.raises(CandidateRejected):
        CandidateRpc(ledger=CandidateQuotaLedger()).propose(
            _proposal(component={"gate": "x"}), context=bad
        )


@pytest.mark.parametrize("clock", [lambda: float("nan"), lambda: float("inf"), lambda: True])
def test_invalid_clock_fails_closed(clock: object) -> None:
    rpc = CandidateRpc(ledger=CandidateQuotaLedger(monotonic=clock))  # type: ignore[arg-type]
    with pytest.raises(CandidateRejected, match="clock"):
        rpc.propose(_proposal(), context=_context())
